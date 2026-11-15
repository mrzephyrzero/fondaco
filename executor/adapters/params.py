# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Plan parameters → driver-level bound parameters, for any driver's marker style.

Plan templates carry `%(name)s` placeholders (plan-dsl §3.1). Drivers that use
another marker (`?` for ODBC, `@name` for BigQuery) get the template with each
placeholder token replaced by the driver's marker and the values passed
separately — the values never enter the SQL text. The boundary has already
proved, by parsing, that every `%(name)s` occurrence is a real placeholder
node (a placeholder-shaped string inside a literal makes the counts disagree
and the template is refused), so this token substitution cannot change what
the statement reads.
"""

from __future__ import annotations

from datetime import date, datetime

from boundary.sql_ast import PLACEHOLDER_RE


class ParamError(ValueError):
    """A step's params do not match its template. Never carries a value."""


def convert_value(spec: dict) -> object:
    kind = spec["type"]
    value = spec["value"]
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "timestamp":
        return datetime.fromisoformat(value)
    if kind == "int" and (isinstance(value, bool) or not isinstance(value, int)):
        raise ParamError("int parameter is not an integer")
    return value


def to_positional(template: str, params: dict) -> tuple[str, list]:
    """`%(a)s … %(b)s … %(a)s` → `? … ? … ?` with values [a, b, a]."""
    values: list = []

    def marker(match) -> str:
        name = match.group(1)
        if name not in params:
            raise ParamError(f"undeclared parameter {name!r}")
        values.append(convert_value(params[name]))
        return "?"

    return PLACEHOLDER_RE.sub(marker, template), values


def to_named(template: str, params: dict, prefix: str = "@") -> tuple[str, dict]:
    """`%(a)s` → `@a`; returns the converted values by name (declared and used only)."""
    used: dict = {}

    def marker(match) -> str:
        name = match.group(1)
        if name not in params:
            raise ParamError(f"undeclared parameter {name!r}")
        used[name] = convert_value(params[name])
        return f"{prefix}{name}"

    return PLACEHOLDER_RE.sub(marker, template), used
