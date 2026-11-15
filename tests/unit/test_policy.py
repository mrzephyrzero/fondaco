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


def test_unknown_table_is_an_absolute_deny():
    # Not merely restricted: no clearance, not even restricted, admits it.
    decision = evaluate(_plan("SELECT x FROM shadow_table"), SCHEMA, "restricted")
    assert decision.allow is False
    assert decision.reason_code == "unknown_relation"


def test_unlabeled_table_is_restricted():
    schema = {"mystery": {"columns": {"x": None}}}
    decision = evaluate(_plan("SELECT x FROM mystery"), schema, "confidential")
    assert decision.allow is False


def test_unknown_label_string_escalates_not_lowers():
    schema = {"weird": {"label": "top-secret", "columns": {}}}
    decision = evaluate(_plan("SELECT x FROM weird"), schema, "confidential")
    assert decision.allow is False


# ── Column-level granularity (label-model.md §4: "columns read") ─────────
#
# The step label is the max over the effective labels of the columns a template
# actually references — in SELECT, WHERE, JOIN … ON, GROUP BY, HAVING, ORDER BY
# and any subquery — with every table read as a floor. A column's effective
# label is max(column, table): a column may raise its table's label, never
# lower it. This replaced the whole-table over-approximation of the regex
# scanner, under which `SELECT department FROM employees` was restricted only
# because `salary` existed.

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


def test_unread_high_column_no_longer_raises_the_label():
    # `salary` is never referenced, so it is never read: the result is internal.
    decision = evaluate(_plan("SELECT department FROM employees"), _MIXED, "internal")
    assert decision.allow is True
    assert decision.plan_label == "internal"


@pytest.mark.parametrize(
    "template",
    [
        "SELECT department FROM employees WHERE salary > %(floor)s",
        "SELECT department FROM employees ORDER BY salary",
        "SELECT department FROM employees GROUP BY department HAVING max(salary) > %(floor)s",
        "SELECT e.name FROM employees e JOIN employees s ON e.salary = s.salary",
        "SELECT department FROM employees WHERE salary IS NOT NULL GROUP BY department",
    ],
)
def test_restricted_column_used_only_as_a_filter_is_read(template):
    # A predicate on a restricted column is an inference channel on it: reading
    # it in WHERE/ORDER BY/HAVING/ON counts exactly like selecting it.
    params = {"floor": {"type": "int", "value": 1}} if "%(floor)s" in template else {}
    plan = _plan(template)
    plan["steps"][0]["params"] = params
    decision = evaluate(plan, _MIXED, "internal")
    assert decision.allow is False
    assert decision.plan_label == "restricted"


def test_table_label_is_a_floor_when_no_column_is_named():
    # count(*) names no column; the table is still read, so its label applies.
    decision = evaluate(_plan("SELECT count(*) AS n FROM employees"), _MIXED, "public")
    assert decision.allow is False
    assert decision.plan_label == "internal"


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


def test_subquery_is_resolved_through_its_alias():
    # Derived tables used to be unresolvable (→ restricted). The AST resolves
    # `sub.name` to products.name: public.
    decision = evaluate(
        _plan("SELECT sub.name FROM (SELECT name FROM products) sub"), SCHEMA, "public"
    )
    assert decision.allow is True
    assert decision.plan_label == "public"


def test_unknown_column_in_a_known_table_is_restricted():
    decision = evaluate(
        _plan("SELECT x FROM (SELECT name FROM products) sub"), SCHEMA, "confidential"
    )
    assert decision.allow is False
    assert decision.plan_label == "restricted"


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


# ── Internal faults are a deny, never an exception (policy.py outer except) ─
#
# `evaluate` promises it never raises. Its inner handlers cover the two faults
# it anticipates (an unknown clearance, a dangling step reference); anything
# else must reach the outer catch-all and come back as a deny. These tests are
# the only thing that executes that catch-all.


def test_unanticipated_fault_from_hostile_input_is_a_deny():
    # `steps` that cannot be iterated raises TypeError inside step_labels —
    # neither LabelError nor KeyError, so no inner handler catches it.
    decision = evaluate({"steps": None}, SCHEMA, "internal")
    assert decision.allow is False
    assert decision.reason_code == "policy_error"
    assert decision.plan_label is None
    assert decision.detail == "internal policy fault: TypeError"


def test_internal_fault_detail_carries_the_type_not_the_message(monkeypatch):
    # A fault raised from inside the engine, carrying something that must not
    # travel: the decision reports the exception's type and nothing else.
    import boundary.policy as policy

    def explode(plan, schema_labels, *args):
        raise RuntimeError("customer 4111-1111-1111-1111")

    monkeypatch.setattr(policy, "step_labels", explode)
    decision = policy.evaluate(_plan("SELECT region FROM orders"), SCHEMA, "internal")
    assert decision.allow is False
    assert decision.reason_code == "policy_error"
    assert "4111" not in decision.detail
    assert decision.detail == "internal policy fault: RuntimeError"
