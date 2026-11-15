# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Google BigQuery adapter (google-cloud-bigquery), strictly read-only.

Defense in depth, outermost first:
  * the service account should hold only `bigquery.dataViewer` on the exposed
    dataset and `bigquery.jobUser` on the billing project (deployment);
  * every statement is first submitted as a dry run, and only a statement
    BigQuery itself reports as `SELECT` is run — DML, DDL and scripts are
    refused before any byte is read;
  * `maximum_bytes_billed` caps cost (the job fails instead of scanning
    more), `job_timeout_ms` and a client-side timeout cap duration, and
    results are fetched with `max_results = max_rows + 1`;
  * plans use bare table names, resolved against one `default_dataset`; a
    qualified `project.dataset.table` never gets here (the boundary refuses
    qualified names as reads outside the annotated schema).

Labels come from table and column descriptions in the `label:<level>
<description>` form; anything unlabelled is `restricted`. Error messages are
reduced to the exception class and HTTP status — BigQuery error text can
quote data values.
"""

from __future__ import annotations

import hashlib
import json
import re

from boundary.policy import query_label
from executor.adapters.annotations import Annotations, apply_annotations
from executor.adapters.contract import (
    AdapterError,
    AnnotatedSchema,
    Capabilities,
    Column,
    LabeledResult,
    Table,
    schema_labels_dict,
)
from executor.adapters.params import ParamError, to_named

DIALECT = "bigquery"
DEFAULT_MAX_BYTES_BILLED = 1_000_000_000  # 1 GB per query
_LABEL_RE = re.compile(r"^label:(public|internal|confidential|restricted)\b\s*(.*)", re.DOTALL)
_PARAM_TYPES = {
    "string": "STRING",
    "int": "INT64",
    "float": "FLOAT64",
    "bool": "BOOL",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}


def _parse_label(description: object) -> tuple[str, str]:
    text = description.strip() if isinstance(description, str) else ""
    match = _LABEL_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return "restricted", text


def _digest(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> str:
    canonical = json.dumps([list(columns), [list(r) for r in rows]], default=str, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sanitize(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    return f"{type(exc).__name__} (status={code if isinstance(code, int) else 'unknown'})"


def _is_timeout(exc: Exception) -> bool:
    name = type(exc).__name__
    return name in ("TimeoutError", "DeadlineExceeded", "Timeout") or "timeout" in name.lower()


class BigQueryAdapter:
    def __init__(
        self,
        project: str,
        dataset: str,
        max_rows: int = 10_000,
        statement_timeout_s: int = 30,
        maximum_bytes_billed: int = DEFAULT_MAX_BYTES_BILLED,
        location: str | None = None,
        annotations: Annotations | None = None,
        client=None,
        bigquery_module=None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", dataset or ""):
            raise AdapterError("connection", "dataset name is not a plain identifier")
        if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project or ""):
            raise AdapterError("connection", "project id is malformed")
        if isinstance(maximum_bytes_billed, bool) or int(maximum_bytes_billed) < 1:
            raise AdapterError("connection", "maximum_bytes_billed must be positive")
        self._project = project
        self._dataset = dataset
        self._max_rows = int(max_rows)
        self._timeout_s = max(1, int(statement_timeout_s))
        self._max_bytes = int(maximum_bytes_billed)
        self._location = location
        self._annotations = annotations or {}
        self._client = client
        self._bq = bigquery_module

    def _module(self):
        if self._bq is None:
            try:
                from google.cloud import bigquery
            except ImportError as exc:
                raise AdapterError(
                    "connection", "BigQuery adapter needs: pip install 'fondaco[bigquery]'"
                ) from exc
            self._bq = bigquery
        return self._bq

    def _get_client(self):
        if self._client is None:
            bq = self._module()
            try:
                self._client = bq.Client(project=self._project, location=self._location)
            except Exception as exc:  # auth / config errors from the SDK
                raise AdapterError("connection", _sanitize(exc)) from exc
        return self._client

    @property
    def _dataset_id(self) -> str:
        return f"{self._project}.{self._dataset}"

    def get_schema(self) -> AnnotatedSchema:
        client = self._get_client()
        try:
            built = []
            for item in client.list_tables(self._dataset_id, timeout=self._timeout_s):
                table = client.get_table(item.reference, timeout=self._timeout_s)
                if getattr(table, "table_type", "TABLE") != "TABLE":
                    continue  # views/external/materialized: not in the annotated schema
                columns = []
                for field in table.schema:
                    label, desc = _parse_label(field.description)
                    columns.append(Column(field.name, str(field.field_type), label, desc))
                label, desc = _parse_label(table.description)
                built.append(
                    apply_annotations(
                        Table(
                            table.table_id,
                            label,
                            tuple(columns),
                            desc,
                            int(table.num_rows or 0),
                        ),
                        self._annotations,
                    )
                )
            return AnnotatedSchema(tables=tuple(built))
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError("schema", _sanitize(exc)) from exc

    def _job_config(self, bq, params: dict, dry_run: bool):
        return bq.QueryJobConfig(
            default_dataset=self._dataset_id,
            query_parameters=[
                bq.ScalarQueryParameter(name, _PARAM_TYPES[kind], value)
                for name, (kind, value) in params.items()
            ],
            maximum_bytes_billed=self._max_bytes,
            job_timeout_ms=self._timeout_s * 1000,
            use_legacy_sql=False,
            dry_run=dry_run,
            use_query_cache=not dry_run,
        )

    def execute(self, step: dict) -> LabeledResult:
        if not isinstance(step, dict) or step.get("type") != "query":
            raise AdapterError("execution", "adapter only executes query steps")
        try:
            sql, values = to_named(step["template"], step["params"])
            params = {name: (step["params"][name]["type"], v) for name, v in values.items()}
            for kind, _ in params.values():
                if kind not in _PARAM_TYPES:
                    raise ParamError("unsupported parameter type")
        except (KeyError, TypeError, ValueError, ParamError) as exc:
            raise AdapterError("execution", f"invalid params: {type(exc).__name__}") from exc

        bq = self._module()
        client = self._get_client()
        # Labels are read fresh for every execution, like the Postgres adapter.
        labels = schema_labels_dict(self.get_schema())
        label = query_label(step["template"], labels, dialect=DIALECT).name.lower()
        try:
            dry = client.query(sql, job_config=self._job_config(bq, params, True))
            if getattr(dry, "statement_type", None) != "SELECT":
                raise AdapterError("execution", "only SELECT statements are executed")
            job = client.query(sql, job_config=self._job_config(bq, params, False))
            result = job.result(max_results=self._max_rows + 1, timeout=self._timeout_s)
            columns = tuple(f.name for f in result.schema)
            fetched = [tuple(row.values()) for row in result]
        except AdapterError:
            raise
        except Exception as exc:
            kind = "timeout" if _is_timeout(exc) else "execution"
            raise AdapterError(kind, _sanitize(exc)) from exc

        if len(fetched) > self._max_rows:
            raise AdapterError("limit_exceeded", f"result exceeds max_rows={self._max_rows}")
        rows = tuple(fetched)
        return LabeledResult(columns, rows, label, len(rows), _digest(columns, rows))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dsl_versions=("v0",),
            param_types=tuple(_PARAM_TYPES),
            max_rows=self._max_rows,
            read_only=True,
        )
