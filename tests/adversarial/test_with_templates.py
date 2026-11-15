# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""plan-dsl v2 §3.1: `WITH` templates — accepted when read-only and non-recursive.

The widening must not open anything: data-modifying CTEs, recursion, and a
final statement other than a SELECT stay refused, and a CTE is labeled by the
columns it actually reads, through any renaming or shadowing.
"""

import pytest

from boundary.policy import evaluate
from boundary.validator import validate_plan

LABELS = {
    "orders": {"label": "internal", "columns": {"id": "internal", "region": "internal"}},
    "customers": {"label": "restricted", "columns": {"id": "internal", "email": "restricted"}},
}


def _plan(template: str) -> dict:
    return {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "q",
        "steps": [
            {"id": "s1", "type": "query", "template": template, "params": {}},
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
        ],
    }


@pytest.mark.parametrize(
    "template",
    [
        "WITH r AS (SELECT region FROM orders) SELECT region FROM r",
        "  with r AS (SELECT region FROM orders), s AS (SELECT region FROM r) SELECT region FROM s",
        "WITH r AS (SELECT region FROM orders) SELECT region FROM r "
        "UNION SELECT region FROM orders",
    ],
)
def test_read_only_cte_is_valid_and_labeled_internal(template):
    plan = _plan(template)
    assert validate_plan(plan).valid is True
    decision = evaluate(plan, LABELS, "internal")
    assert decision.allow is True
    assert decision.plan_label == "internal"


@pytest.mark.parametrize(
    "template",
    [
        "WITH x AS (SELECT email AS region FROM customers) SELECT region FROM x",
        "WITH orders AS (SELECT email AS region FROM customers) SELECT region FROM orders",
    ],
)
def test_cte_hiding_a_restricted_column_is_denied(template):
    plan = _plan(template)
    assert validate_plan(plan).valid is True
    decision = evaluate(plan, LABELS, "internal")
    assert decision.allow is False
    assert decision.plan_label == "restricted"


@pytest.mark.parametrize(
    ("template", "code"),
    [
        (
            "WITH RECURSIVE n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n) SELECT i FROM n",
            "forbidden_construct",
        ),
        ("WITH x AS (DELETE FROM orders RETURNING id) SELECT id FROM x", "forbidden_keyword"),
        ("WITH x AS (SELECT id FROM orders) DELETE FROM orders", "forbidden_keyword"),
        ("WITHOUT_TABLE", "template_not_select"),
    ],
)
def test_unsafe_with_forms_are_rejected(template, code):
    result = validate_plan(_plan(template))
    assert result.valid is False
    assert code in {e.code for e in result.errors}


def test_cte_over_unknown_relation_is_denied():
    plan = _plan("WITH x AS (SELECT usename FROM pg_catalog.pg_user) SELECT usename FROM x")
    assert validate_plan(plan).valid is False
    assert evaluate(plan, LABELS, "restricted").reason_code == "unknown_relation"
