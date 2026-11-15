# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Labels kept in a file, for sources that have no place to store them.

Postgres, SQL Server and BigQuery carry labels in the catalog itself (column
comments / descriptions). An embedded DuckDB file or a folder of CSVs does
not, so the DuckDB adapter reads them from a reviewed `schema.yaml` (or
`.json`) in the `fondaco-annotations` format that `fondaco discover` writes:

    format: fondaco-annotations
    version: 1
    tables:
      - name: employees
        label: internal
        columns:
          - {name: department, label: public, enum_values: [Sales, R&D]}
          - {name: salary, label: confidential}

Fail closed, in the same direction as every other label source:
  * a table or column missing from the file is `restricted`;
  * a label that is not exactly one of the four levels is `restricted`;
  * enum metadata is kept only where the contract allows it (effective label
    public/internal, ≤ 30 short values) and dropped column by column otherwise;
  * a file that cannot be read or is not in this format is an error at load
    time — running without the labels would silently restrict everything,
    and running with half of them would hide the mistake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_MAX_FILE_BYTES = 5 * 1024 * 1024
_LEVELS = frozenset({"public", "internal", "confidential", "restricted"})


class SchemaFileError(ValueError):
    """The schema file is unreadable or not a fondaco-annotations document."""


@dataclass(frozen=True)
class ColumnSpec:
    label: str = "restricted"
    comment: str = ""
    enum_values: tuple[str, ...] | None = None
    business_description: str | None = None


@dataclass(frozen=True)
class TableSpec:
    label: str = "restricted"
    comment: str = ""
    columns: dict[str, ColumnSpec] = field(default_factory=dict)


SchemaFile = dict[str, TableSpec]


def _level(raw: object) -> str:
    return raw if isinstance(raw, str) and raw in _LEVELS else "restricted"


def _text(raw: object) -> str:
    return raw if isinstance(raw, str) else ""


def _parse(path: Path, text: str) -> object:
    if path.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError as exc:
            raise SchemaFileError(
                "YAML schema files need PyYAML: pip install 'fondaco[duckdb]'"
            ) from exc
        try:
            return yaml.safe_load(text)  # safe_load: no object construction
        except yaml.YAMLError as exc:
            raise SchemaFileError(f"schema file is not valid YAML: {type(exc).__name__}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaFileError("schema file is not valid JSON") from exc


def load_schema_file(path: str | Path) -> SchemaFile:
    path = Path(path)
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise SchemaFileError("schema file is larger than 5 MiB")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SchemaFileError(f"cannot read schema file: {type(exc).__name__}") from exc
    doc = _parse(path, text)
    if not isinstance(doc, dict) or doc.get("format") != "fondaco-annotations":
        raise SchemaFileError("not a fondaco-annotations document")
    tables = doc.get("tables")
    if not isinstance(tables, list):
        raise SchemaFileError("schema file: 'tables' must be a list")

    out: SchemaFile = {}
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            continue
        if table["name"] in out:
            raise SchemaFileError(f"schema file: table {table['name']!r} listed twice")
        columns: dict[str, ColumnSpec] = {}
        for column in table.get("columns") or ():
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                continue
            if column["name"] in columns:
                raise SchemaFileError(
                    f"schema file: column {table['name']}.{column['name']} listed twice"
                )
            enum_values = column.get("enum_values")
            description = column.get("business_description")
            columns[column["name"]] = ColumnSpec(
                label=_level(column.get("label")),
                comment=_text(column.get("comment")),
                enum_values=tuple(enum_values) if isinstance(enum_values, list) else None,
                business_description=description if isinstance(description, str) else None,
            )
        out[table["name"]] = TableSpec(
            label=_level(table.get("label")),
            comment=_text(table.get("comment")),
            columns=columns,
        )
    return out
