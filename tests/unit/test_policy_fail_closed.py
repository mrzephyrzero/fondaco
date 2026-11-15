# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Fail-closed branches of the AST label analyser (boundary/policy.py).

Every malformed schema shape and every internal fault must come out
`restricted` (or denied) — never an exception, never a lower label.
"""

import pytest

import boundary.policy as policy
from boundary.policy import Label, analyze_query, evaluate, query_label

GOOD = {"orders": {"label": "internal", "columns": {"region": "internal"}}}


@pytest.mark.parametrize(
    "schema",
    [
        None,
        [],
        "orders",
        {"orders": "internal"},  # table entry is not a dict
        {"orders": {"label": "internal", "columns": ["region"]}},  # columns not a dict
        {"orders": {"label": "internal", "columns": {1: "internal"}}},  # non-str column
        {1: {"label": "internal", "columns": {"region": "internal"}}},  # non-str table
        {"orders": {"label": "internal", "columns": {"region": "bogus"}}},  # unknown label
    ],
)
def test_malformed_schema_never_labels_below_restricted(schema):
    assert query_label("SELECT region FROM orders", schema) == Label.RESTRICTED


def test_constant_select_reads_no_relation_and_is_restricted():
    analysis = analyze_query("SELECT 1", GOOD)
    assert analysis.label == Label.RESTRICTED
    assert analysis.reason == "no_relation"


def test_internal_analysis_fault_is_restricted(monkeypatch):
    def explode(*args):
        raise RuntimeError("customer 4111-1111-1111-1111")

    monkeypatch.setattr(policy, "_analyze", explode)
    analysis = analyze_query("SELECT region FROM orders", GOOD)
    assert analysis.label == Label.RESTRICTED
    assert analysis.resolved is False
    assert "4111" not in analysis.reason


def test_unparsable_template_is_restricted_not_raised():
    assert query_label("SELEC region FROM orders", GOOD) == Label.RESTRICTED
    assert query_label(None, GOOD) == Label.RESTRICTED  # type: ignore[arg-type]


def test_column_absent_from_labels_is_restricted():
    # The column exists in the template but not in the label map.
    assert query_label("SELECT status FROM orders", GOOD) == Label.RESTRICTED


def test_projection_alias_in_order_by_does_not_over_restrict():
    t = "SELECT region AS r FROM orders ORDER BY r"
    assert query_label(t, GOOD) == Label.INTERNAL


def test_good_schema_resolves():
    decision = evaluate(
        {
            "steps": [
                {"id": "s1", "type": "query", "template": "SELECT region FROM orders"},
                {"id": "s2", "type": "present", "input": "s1"},
            ]
        },
        GOOD,
        "internal",
    )
    assert decision.allow is True
