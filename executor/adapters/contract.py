# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Adapter contract types — a transcription of design/adapter-contract.md §2.

These shapes are frozen by the interface; do not extend them here. The
`comment` fields carry customer-controlled text and are untrusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ADAPTER_ERROR_KINDS = ("connection", "timeout", "limit_exceeded", "execution", "schema")


class AdapterError(Exception):
    """All adapter failures. `message` must never embed row data or param values."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in ADAPTER_ERROR_KINDS:
            kind = "execution"
        self.kind = kind
        self.message = message
        super().__init__(f"{kind}: {message}")


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    label: str  # one of the four levels; adapters default missing annotations to "restricted"
    comment: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    label: str
    columns: tuple[Column, ...]
    comment: str = ""
    row_count: int = 0  # coarse statistic for planner context; never row data


@dataclass(frozen=True)
class AnnotatedSchema:
    tables: tuple[Table, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class LabeledResult:
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    label: str
    row_count: int
    digest: str


@dataclass(frozen=True)
class Capabilities:
    dsl_versions: tuple[str, ...]
    param_types: tuple[str, ...]
    max_rows: int
    read_only: bool


class Adapter(Protocol):
    def get_schema(self) -> AnnotatedSchema: ...

    def execute(self, step: dict) -> LabeledResult: ...

    def capabilities(self) -> Capabilities: ...


def schema_labels_dict(schema: AnnotatedSchema) -> dict:
    """Adapt AnnotatedSchema to the plain shape boundary.policy consumes."""
    return {
        table.name: {
            "label": table.label,
            "columns": {column.name: column.label for column in table.columns},
        }
        for table in schema.tables
    }
