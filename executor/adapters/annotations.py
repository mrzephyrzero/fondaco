# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Semantic-metadata overlay for adapters (adapter-contract v1 §2.1).

A Data Owner reviews the proposal written by `fondaco discover`, edits it, and
points `FONDACO_SCHEMA_ANNOTATIONS` at the result. The overlay contributes
*only* `enum_values` and `business_description`. It never sets, raises or
lowers a label: labels come from the source (Postgres comments) and stay the
single source of truth. Metadata lands on a column only when the effective
label read from the source is public or internal; anything else in the file
is ignored, column by column, so a stale or wrong proposal can never put a
confidential value into the planner prompt.

A file that cannot be read or parsed is a configuration error and is raised
at load time — silently running without the overlay would hide the mistake.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from executor.adapters.contract import (
    Column,
    SchemaMetadataError,
    Table,
    enum_metadata_allowed,
)

ANNOTATIONS_ENV = "FONDACO_SCHEMA_ANNOTATIONS"
_MAX_FILE_BYTES = 5 * 1024 * 1024

# (table, column) -> {"enum_values": [...] | None, "business_description": str | None}
Annotations = dict[tuple[str, str], dict]


class AnnotationsError(ValueError):
    """The overlay file is unreadable or not in the fondaco-annotations format."""


def load_annotations(path: str | Path) -> Annotations:
    path = Path(path)
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise AnnotationsError("annotations file is larger than 5 MiB")
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnnotationsError(f"cannot read annotations: {type(exc).__name__}") from exc
    if not isinstance(doc, dict) or doc.get("format") != "fondaco-annotations":
        raise AnnotationsError("not a fondaco-annotations document")
    tables = doc.get("tables")
    if not isinstance(tables, list):
        raise AnnotationsError("annotations: 'tables' must be a list")

    out: Annotations = {}
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("name"), str):
            continue
        for column in table.get("columns") or ():
            if not isinstance(column, dict) or not isinstance(column.get("name"), str):
                continue
            enum_values = column.get("enum_values")
            description = column.get("business_description")
            if enum_values is None and description is None:
                continue
            out[(table["name"], column["name"])] = {
                "enum_values": enum_values,
                "business_description": description,
            }
    return out


def annotations_from_env(env: dict | None = None) -> Annotations:
    env = os.environ if env is None else env
    path = (env.get(ANNOTATIONS_ENV) or "").strip()
    return load_annotations(path) if path else {}


def apply_annotations(table: Table, annotations: Annotations) -> Table:
    """Return `table` with overlay metadata on eligible columns.

    Eligibility is decided on the labels the *source* reports. Metadata the
    contract refuses (too many values, too long, wrong type) is dropped for
    that column only; the column itself is kept unchanged.
    """
    if not annotations:
        return table
    columns: list[Column] = []
    changed = False
    for column in table.columns:
        meta = annotations.get((table.name, column.name))
        if meta is None or not enum_metadata_allowed(table.label, column.label):
            columns.append(column)
            continue
        try:
            columns.append(
                replace(
                    column,
                    enum_values=meta["enum_values"],
                    business_description=meta["business_description"],
                )
            )
            changed = True
        except SchemaMetadataError:
            columns.append(column)
    return replace(table, columns=tuple(columns)) if changed else table
