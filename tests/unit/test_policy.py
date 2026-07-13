# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
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
