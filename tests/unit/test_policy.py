# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Policy engine: label escalation attempts must deny; nothing declassifies."""

import pytest

from boundary.policy import Label, LabelError, evaluate

SCHEMA = {
    "products": {"label": "public", "columns": {"name": "public", "price": "public"}},
    "orders": {"label": "internal", "columns": {"id": "internal", "region": "internal"}},
    "salaries": {"label": "restricted", "columns": {"amount": "restricted"}},
}


def _plan(template: str, with_aggregate: bool = False) -> dict:
    steps = [{"id": "s1", "type": "query", "template": template, "params": {}}]
    if with_aggregate:
        steps.append(
            {
                "id": "s2",
                "type": "aggregate",
                "input": "s1",
                "group_by": [],
                "ops": [{"op": "avg", "column": "amount", "as": "avg_amount"}],
            }
        )
    steps.append(
        {
            "id": f"s{len(steps) + 1}",
            "type": "present",
            "input": f"s{len(steps)}",
            "format": "table",
            "title": "t",
        }
    )
    return {"dsl_version": "v0", "plan_id": "x", "question": "q", "steps": steps}


def test_allow_when_label_within_clearance():
    decision = evaluate(_plan("SELECT region FROM orders"), SCHEMA, "internal")
    assert decision.allow is True
    assert decision.plan_label == "internal"
    assert decision.reason_code == "allow"


def test_deny_when_label_exceeds_clearance():
    decision = evaluate(_plan("SELECT amount FROM salaries"), SCHEMA, "confidential")
    assert decision.allow is False
    assert decision.reason_code == "label_exceeds_clearance"


def test_unknown_table_is_restricted():
    decision = evaluate(_plan("SELECT x FROM shadow_table"), SCHEMA, "confidential")
    assert decision.allow is False


def test_unlabeled_table_is_restricted():
    schema = {"mystery": {"columns": {"x": None}}}
    decision = evaluate(_plan("SELECT x FROM mystery"), schema, "confidential")
    assert decision.allow is False


def test_unknown_label_string_escalates_not_lowers():
    schema = {"weird": {"label": "top-secret", "columns": {}}}
    decision = evaluate(_plan("SELECT x FROM weird"), schema, "confidential")
    assert decision.allow is False


# ── A column may raise its table's label; it may never lower it ────────────
#
# `_table_label` takes the max over the table label *and every column of that
# table*, so a single high column pulls the whole table up. Until these tests
# existed no fixture in the repo had a column labeled above its table, which
# left the raising branch unexecuted — in the function that decides every label.
#
# This is also where label-model.md §4 ("max over every column read by the
# template") and the implementation visibly part company: the scan does not
# work out which columns a template reads, so the label is computed over *all*
# columns of every referenced table. That over-approximates — it can only raise
# a label, never lower one — and the second test below pins that behaviour.

_MIXED = {
    "employees": {
        "label": "internal",
        "columns": {"name": "internal", "department": "internal", "salary": "restricted"},
    }
}


def test_column_label_raises_the_table_label():
    decision = evaluate(_plan("SELECT salary FROM employees"), _MIXED, "internal")
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_unread_high_column_still_raises_the_label():
    # `salary` is not in the select list. The label is restricted anyway: this is
    # the documented over-approximation, not a bug. It over-restricts, which is
    # the safe direction — but it does mean a table is only as usable as its
    # most sensitive column.
    decision = evaluate(_plan("SELECT department FROM employees"), _MIXED, "internal")
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_column_label_below_the_table_label_changes_nothing():
    # The mirror case: a low column cannot pull a restricted table down.
    schema = {"salaries": {"label": "restricted", "columns": {"id": "public"}}}
    decision = evaluate(_plan("SELECT id FROM salaries"), schema, "confidential")
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_aggregation_does_not_declassify():
    decision = evaluate(
        _plan("SELECT amount FROM salaries", with_aggregate=True), SCHEMA, "confidential"
    )
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_restricted_result_at_restricted_clearance_allows():
    decision = evaluate(
        _plan("SELECT amount FROM salaries", with_aggregate=True), SCHEMA, "restricted"
    )
    assert decision.allow is True


def test_subquery_is_unresolvable_hence_restricted():
    decision = evaluate(
        _plan("SELECT x FROM (SELECT name FROM products) sub"), SCHEMA, "confidential"
    )
    assert decision.allow is False


def test_join_takes_max_of_both_tables():
    decision = evaluate(
        _plan("SELECT o.region FROM orders o JOIN salaries s ON o.id = s.id"), SCHEMA, "internal"
    )
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_unknown_clearance_denies():
    decision = evaluate(_plan("SELECT name FROM products"), SCHEMA, "root")
    assert decision.allow is False
    assert decision.reason_code == "unknown_clearance"


def test_malformed_plan_denies_not_raises():
    decision = evaluate({"steps": [{"type": "present", "id": "s1"}]}, SCHEMA, "internal")
    assert decision.allow is False


def test_label_order_is_total():
    assert Label.PUBLIC < Label.INTERNAL < Label.CONFIDENTIAL < Label.RESTRICTED


def test_label_parse_is_strict():
    with pytest.raises(LabelError):
        Label.parse("Public ")
    with pytest.raises(LabelError):
        Label.parse(None)
