# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Label/egress policy engine (design/label-model.md §2–5).

Evaluates a *validated* plan against schema labels and an endpoint
clearance. Deny is the default on every path: unknown labels, unknown
tables, unresolvable SQL references, and internal faults all deny.

Query-step labeling is a sound over-approximation: the step label is the
max over the effective labels of every column of every table referenced
in FROM/JOIN. This can only raise a label relative to label-model.md §4
("columns read"), never lower it (see DECISIONS.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

_FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\b\s*([A-Za-z_][A-Za-z0-9_]*)?", re.IGNORECASE)


class LabelError(ValueError):
    """Raised for any string that is not exactly one of the four levels."""


class Label(IntEnum):
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3

    @classmethod
    def parse(cls, raw: object) -> Label:
        if isinstance(raw, str) and raw in _LEVELS:
            return _LEVELS[raw]
        raise LabelError(f"unknown label: {raw!r}")


_LEVELS: dict[str, Label] = {
    "public": Label.PUBLIC,
    "internal": Label.INTERNAL,
    "confidential": Label.CONFIDENTIAL,
    "restricted": Label.RESTRICTED,
}


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    plan_label: str | None
    reason_code: str
    detail: str


def _deny(reason_code: str, detail: str, plan_label: str | None = None) -> PolicyDecision:
    return PolicyDecision(
        allow=False, plan_label=plan_label, reason_code=reason_code, detail=detail
    )


def _parse_or_restricted(raw: object) -> Label:
    """Missing/unknown labels degrade upward, never downward (fail closed)."""
    if raw is None:
        return Label.RESTRICTED
    try:
        return Label.parse(raw)
    except LabelError:
        return Label.RESTRICTED


def _table_label(table: dict) -> Label:
    """Max over the table label and all its column labels (over-approximation)."""
    base = _parse_or_restricted(table.get("label"))
    label = base
    columns = table.get("columns") or {}
    for column_label in columns.values():
        label = max(label, max(_parse_or_restricted(column_label), base))
    return label


def _referenced_tables(template: str) -> list[str] | None:
    """Table names after FROM/JOIN, or None if any reference is unresolvable."""
    names: list[str] = []
    for match in _FROM_JOIN_RE.finditer(template):
        if match.group(1) is None:
            return None  # subquery, quoted identifier, or other construct we cannot resolve
        names.append(match.group(1))
    return names


def query_label(template: str, schema_labels: dict) -> Label:
    """Public labeling helper, shared with the executor (STATE.md Phase-2 note).

    Sound over-approximation of label-model.md §4: max over every column of
    every FROM/JOIN table; unresolvable → restricted. Never lowers a label.
    """
    tables = _referenced_tables(template)
    if tables is None or not tables:
        return Label.RESTRICTED
    label = Label.PUBLIC
    for name in tables:
        table = schema_labels.get(name)
        if not isinstance(table, dict):
            return Label.RESTRICTED
        label = max(label, _table_label(table))
    return label


def step_labels(plan: dict, schema_labels: dict) -> dict[str, Label]:
    """Per-step labels for a validated plan (query → scan; others inherit).

    Raises KeyError on dangling references — callers must treat that as deny.
    Public so the approval UI renders exactly what policy computed.
    """
    labels: dict[str, Label] = {}
    for step in plan["steps"]:
        if step["type"] == "query":
            labels[step["id"]] = query_label(step["template"], schema_labels)
        else:  # aggregate / present inherit; aggregation never declassifies
            labels[step["id"]] = labels[step["input"]]
    return labels


def evaluate(plan: dict, schema_labels: dict, clearance: object) -> PolicyDecision:
    """Decide egress for a validated plan. Never raises; any fault → deny."""
    try:
        try:
            clearance_label = Label.parse(clearance)
        except LabelError:
            return _deny("unknown_clearance", f"clearance is not a known level: {clearance!r}")

        try:
            labels = step_labels(plan, schema_labels)
        except KeyError:
            return _deny("missing_step", "a step references an unknown input")

        plan_label = labels[plan["steps"][-1]["id"]]
        result_name = plan_label.name.lower()
        clearance_name = clearance_label.name.lower()
        if plan_label <= clearance_label:
            return PolicyDecision(
                allow=True,
                plan_label=result_name,
                reason_code="allow",
                detail=f"result label {result_name} <= clearance {clearance_name}",
            )
        return _deny(
            "label_exceeds_clearance",
            f"result label {result_name} > clearance {clearance_name}",
            plan_label=result_name,
        )
    except Exception as exc:  # fail closed: an internal fault is a deny
        return _deny("policy_error", f"internal policy fault: {type(exc).__name__}")
