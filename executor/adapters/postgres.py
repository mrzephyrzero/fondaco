# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""PostgreSQL adapter implementing design/adapter-contract.md.

Read-only defense in depth: the demo compose stack connects as a role
without write grants (demo/dataset/init/03_readonly_role.sql), and every
connection additionally sets default_transaction_read_only=on plus a
statement timeout. Error messages are sanitized to exception class name
and SQLSTATE — driver text can embed data values and must not leak.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime

import psycopg

from boundary.policy import query_label
from executor.adapters.contract import (
    AdapterError,
    AnnotatedSchema,
    Capabilities,
    Column,
    LabeledResult,
    Table,
    schema_labels_dict,
)

_LABEL_COMMENT_RE = re.compile(
    r"^label:(public|internal|confidential|restricted)\b\s*(.*)", re.DOTALL
)

_SCHEMA_SQL = """
SELECT
    c.relname AS table_name,
    obj_description(c.oid, 'pg_class') AS table_comment,
    greatest(c.reltuples::bigint, 0) AS row_count,
    a.attname AS column_name,
    format_type(a.atttypid, a.atttypmod) AS sql_type,
    col_description(c.oid, a.attnum) AS column_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = 'public'
  AND c.relkind = 'r'
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


def _parse_label(comment: str | None) -> tuple[str, str]:
    """Split a source comment into (label, description). Missing/unparsable → restricted."""
    if comment:
        match = _LABEL_COMMENT_RE.match(comment.strip())
        if match:
            return match.group(1), match.group(2).strip()
    return "restricted", (comment or "").strip()


def _convert_param(spec: dict) -> object:
    if spec["type"] == "date":
        return date.fromisoformat(spec["value"])
    if spec["type"] == "timestamp":
        return datetime.fromisoformat(spec["value"])
    return spec["value"]


def _digest(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> str:
    canonical = json.dumps([list(columns), [list(r) for r in rows]], default=str, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class PostgresAdapter:
    def __init__(
        self,
        dsn: str,
        max_rows: int = 10_000,
        statement_timeout_s: int = 30,
    ) -> None:
        self._dsn = dsn
        self._max_rows = max_rows
        self._timeout_ms = statement_timeout_s * 1000
        self._schema_labels: dict | None = None  # lazy cache; labels change only with the schema

    def _connect(self) -> psycopg.Connection:
        try:
            return psycopg.connect(
                self._dsn,
                connect_timeout=10,
                options=(
                    f"-c default_transaction_read_only=on -c statement_timeout={self._timeout_ms}"
                ),
            )
        except psycopg.Error as exc:
            raise AdapterError("connection", _sanitize(exc)) from exc

    def get_schema(self) -> AnnotatedSchema:
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
                rows = cur.fetchall()
        except AdapterError:
            raise
        except psycopg.Error as exc:
            raise AdapterError("schema", _sanitize(exc)) from exc

        tables: dict[str, dict] = {}
        for table_name, table_comment, row_count, column_name, sql_type, column_comment in rows:
            entry = tables.setdefault(
                table_name,
                {"comment": table_comment, "row_count": int(row_count), "columns": []},
            )
            col_label, col_desc = _parse_label(column_comment)
            entry["columns"].append(
                Column(name=column_name, sql_type=sql_type, label=col_label, comment=col_desc)
            )
        built = []
        for name, entry in tables.items():
            table_label, table_desc = _parse_label(entry["comment"])
            built.append(
                Table(
                    name=name,
                    label=table_label,
                    columns=tuple(entry["columns"]),
                    comment=table_desc,
                    row_count=entry["row_count"],
                )
            )
        return AnnotatedSchema(tables=tuple(built))

    def execute(self, step: dict) -> LabeledResult:
        if not isinstance(step, dict) or step.get("type") != "query":
            raise AdapterError("execution", "adapter only executes query steps")
        try:
            params = {name: _convert_param(spec) for name, spec in step["params"].items()}
        except (KeyError, TypeError, ValueError) as exc:
            raise AdapterError("execution", f"invalid params: {type(exc).__name__}") from exc

        if self._schema_labels is None:
            self._schema_labels = schema_labels_dict(self.get_schema())
        label = query_label(step["template"], self._schema_labels).name.lower()

        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(step["template"], params)
                if cur.description is None:
                    raise AdapterError("execution", "statement returned no result set")
                columns = tuple(d.name for d in cur.description)
                fetched = cur.fetchmany(self._max_rows + 1)
        except AdapterError:
            raise
        except psycopg.errors.QueryCanceled as exc:
            raise AdapterError("timeout", _sanitize(exc)) from exc
        except psycopg.Error as exc:
            raise AdapterError("execution", _sanitize(exc)) from exc

        if len(fetched) > self._max_rows:
            raise AdapterError("limit_exceeded", f"result exceeds max_rows={self._max_rows}")

        rows = tuple(tuple(row) for row in fetched)
        return LabeledResult(
            columns=columns,
            rows=rows,
            label=label,
            row_count=len(rows),
            digest=_digest(columns, rows),
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dsl_versions=("v0",),
            param_types=("string", "int", "float", "bool", "date", "timestamp"),
            max_rows=self._max_rows,
            read_only=True,
        )


def _sanitize(exc: psycopg.Error) -> str:
    """Class name + SQLSTATE only — driver messages can contain data values."""
    sqlstate = getattr(exc, "sqlstate", None) or "unknown"
    return f"{type(exc).__name__} (sqlstate={sqlstate})"
