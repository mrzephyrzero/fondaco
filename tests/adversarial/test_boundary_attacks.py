# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Phase 7 adversarial suite — attacks on the boundary and their outcomes.

Each test corresponds to an entry in design/threat-model.md. The headline is
CRIT-1: an implicit comma-join that hid a restricted table from the label
scanner (row data would cross the boundary). It is fixed; these tests are the
regression guard.
"""

import json

import pytest

from boundary.policy import Label, evaluate, query_label
from boundary.validator import validate_plan

LABELS = {
    "orders": {
        "label": "internal",
        "columns": {"region": "internal", "customer_id": "internal", "total_amount": "internal"},
    },
    "customers": {
        "label": "restricted",
        "columns": {"email": "restricted", "name": "restricted", "city": "confidential"},
    },
    "products": {"label": "public", "columns": {"category": "public"}},
}

COMMA_JOIN_ATTACKS = [
    "SELECT c.email FROM orders o, customers c WHERE o.customer_id = c.id",
    "SELECT email FROM orders, customers",
    "SELECT c.email FROM orders AS o, customers AS c",
    "SELECT c.name FROM products p, customers c",
]


# ── CRIT-1: comma-join label bypass (fixed) ────────────────────────────────


@pytest.mark.parametrize("template", COMMA_JOIN_ATTACKS)
def test_comma_join_is_labeled_restricted(template):
    # The hidden restricted table must force the whole query to restricted.
    assert query_label(template, LABELS) == Label.RESTRICTED


def test_comma_join_plan_is_policy_denied():
    plan = {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "exfil",
        "steps": [
            {
                "id": "s1",
                "type": "query",
                "template": "SELECT c.email FROM orders o, customers c WHERE o.customer_id = c.id",
                "params": {},
            },
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "x"},
        ],
    }
    # The attack plan is structurally VALID — the boundary that stops it is policy.
    assert validate_plan(plan).valid is True
    decision = evaluate(plan, LABELS, "internal")
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_legit_templates_are_not_over_restricted():
    # The fix must not deny the real demo queries (select-list commas, to_char).
    assert query_label("SELECT region, total_amount FROM orders", LABELS) == Label.INTERNAL
    assert (
        query_label("SELECT to_char(order_date, 'YYYY-MM') AS m FROM orders", LABELS)
        == Label.INTERNAL
    )
    assert query_label("SELECT category FROM products", LABELS) == Label.PUBLIC


# ── Other label-escalation vectors (already sound; regression) ─────────────


def test_explicit_join_takes_max_over_all_tables():
    t = "SELECT o.region FROM orders o JOIN customers c ON o.customer_id = c.id"
    assert query_label(t, LABELS) == Label.RESTRICTED


def test_subquery_in_select_reading_restricted_is_restricted():
    t = "SELECT (SELECT email FROM customers LIMIT 1) AS x FROM orders"
    assert query_label(t, LABELS) == Label.RESTRICTED


def test_union_from_restricted_is_restricted():
    t = "SELECT region FROM orders UNION SELECT email FROM customers"
    assert query_label(t, LABELS) == Label.RESTRICTED


def test_schema_qualified_and_quoted_names_fail_closed():
    # Unresolved by the scanner → restricted (fail closed over-restriction).
    assert query_label("SELECT email FROM public.customers", LABELS) == Label.RESTRICTED
    assert query_label('SELECT email FROM "customers"', LABELS) == Label.RESTRICTED
    # Unknown / case-variant table also over-restricts rather than leaking.
    assert query_label("SELECT x FROM CUSTOMERS", LABELS) == Label.RESTRICTED


# ── Repair loop carries no data ────────────────────────────────────────────


def test_validation_errors_never_echo_param_values():
    canary = "ZZ-CANARY-9931-VALUE"
    plan = {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "q",
        "steps": [
            {
                "id": "s1",
                "type": "query",
                "template": "SELECT id FROM orders WHERE id = %(oid)s",
                "params": {"oid": {"type": "int", "value": canary}},  # type mismatch
            },
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
        ],
    }
    result = validate_plan(plan)
    assert result.valid is False
    blob = json.dumps([{"code": e.code, "path": e.path, "detail": e.detail} for e in result.errors])
    # The repair loop feeds exactly this back to the planner — it must not carry
    # the offending value, only the machine-readable reason.
    assert canary not in blob
    assert "param_type_mismatch" in blob
