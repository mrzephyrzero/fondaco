# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Adapter contract types — a transcription of design/adapter-contract.md §2.

These shapes are frozen by the interface; do not extend them here without a
new version of the document. The enum metadata below is the one exception in
flight: it implements the v1 proposal filed in STATE.md under
INTERFACE_CHANGE_REQUEST and is valid only once the maintainer approves it.
The `comment`, `enum_values` and `business_description` fields carry
customer-controlled text and are untrusted.

The proposed v1 adds the only values the contract would let reach the planner:
`enum_values` — the closed set of codes a low-cardinality column holds — and
the `business_description` that explains them. Both are refused at
construction time on any column whose effective label (max of column and
table, label-model.md §3) is above `internal`, so an adapter cannot build a
schema that would put confidential or restricted values into a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ADAPTER_ERROR_KINDS = ("connection", "timeout", "limit_exceeded", "execution", "schema")

# Proposed adapter-contract.md v1 §2.1 — semantic metadata limits (see STATE.md).
ENUM_MAX_VALUES = 30
ENUM_MAX_VALUE_LENGTH = 64
BUSINESS_DESCRIPTION_MAX_LENGTH = 1000
ENUM_METADATA_LABELS = frozenset({"public", "internal"})

_LABEL_ORDER = ("public", "internal", "confidential", "restricted")


def _effective_label(*labels: object) -> str:
    """Max of the given labels; anything unknown or missing is restricted."""
    rank = 0
    for label in labels:
        if label not in _LABEL_ORDER:
            return "restricted"
        rank = max(rank, _LABEL_ORDER.index(label))
    return _LABEL_ORDER[rank]


def enum_metadata_allowed(table_label: object, column_label: object) -> bool:
    """True iff enum_values/business_description may exist on this column."""
    return _effective_label(table_label, column_label) in ENUM_METADATA_LABELS


class AdapterError(Exception):
    """All adapter failures. `message` must never embed row data or param values."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in ADAPTER_ERROR_KINDS:
            kind = "execution"
        self.kind = kind
        self.message = message
        super().__init__(f"{kind}: {message}")


class SchemaMetadataError(ValueError):
    """Semantic metadata that the contract forbids (wrong label, too many values, …)."""


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    label: str  # one of the four levels; adapters default missing annotations to "restricted"
    comment: str = ""
    # Proposed v1 §2.1: the complete, closed set of values of a public/internal column
    # with at most ENUM_MAX_VALUES distinct values; None when not provided.
    enum_values: tuple[str, ...] | None = None
    # Proposed v1 §2.1: what those values mean, e.g. "01=Open, 02=In progress".
    business_description: str | None = None

    def __post_init__(self) -> None:
        if self.enum_values is None and self.business_description is None:
            return
        if self.label not in ENUM_METADATA_LABELS:
            raise SchemaMetadataError(
                f"column {self.name!r}: enum metadata forbidden on label {self.label!r}"
            )
        if self.enum_values is not None:
            values = self.enum_values
            if isinstance(values, str) or not isinstance(values, list | tuple):
                raise SchemaMetadataError(f"column {self.name!r}: enum_values must be a list")
            if not values or len(values) > ENUM_MAX_VALUES:
                raise SchemaMetadataError(
                    f"column {self.name!r}: enum_values must hold 1..{ENUM_MAX_VALUES} values"
                )
            for value in values:
                if not isinstance(value, str) or len(value) > ENUM_MAX_VALUE_LENGTH:
                    raise SchemaMetadataError(
                        f"column {self.name!r}: enum values must be strings of at most "
                        f"{ENUM_MAX_VALUE_LENGTH} characters"
                    )
            if len(set(values)) != len(values):
                raise SchemaMetadataError(f"column {self.name!r}: enum_values must be distinct")
            object.__setattr__(self, "enum_values", tuple(values))
        description = self.business_description
        if description is not None and (
            not isinstance(description, str) or len(description) > BUSINESS_DESCRIPTION_MAX_LENGTH
        ):
            raise SchemaMetadataError(
                f"column {self.name!r}: business_description must be a string of at most "
                f"{BUSINESS_DESCRIPTION_MAX_LENGTH} characters"
            )


@dataclass(frozen=True)
class Table:
    name: str
    label: str
    columns: tuple[Column, ...]
    comment: str = ""
    row_count: int = 0  # coarse statistic for planner context; never row data

    def __post_init__(self) -> None:
        # A column's effective label is max(column, table): an internal column
        # of a confidential table is confidential, so its values may not cross.
        for column in self.columns:
            has_metadata = column.enum_values is not None or column.business_description is not None
            if has_metadata and not enum_metadata_allowed(self.label, column.label):
                raise SchemaMetadataError(
                    f"column {self.name}.{column.name}: enum metadata forbidden under "
                    f"table label {self.label!r}"
                )


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
