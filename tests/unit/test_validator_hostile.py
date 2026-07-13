# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Checkpoint P1: negative-case suite — every hostile input must be rejected.

Each case mutates the known-good plan (or replaces it outright) and names
the validation error code it must trigger (None = any error accepted).
"""

import copy

import pytest

from boundary.validator import validate_plan
from tests.unit.conftest import VALID_PLAN


def _mutated(mutate):
    plan = copy.deepcopy(VALID_PLAN)
    mutate(plan)
    return plan


def _with_template(template, params=None):
    def mutate(plan):
        plan["steps"][0]["template"] = template
        plan["steps"][0]["params"] = params if params is not None else {}

    return _mutated(mutate)


def _hostile_cases() -> list[tuple[str, object, str | None]]:
    cases: list[tuple[str, object, str | None]] = []

    cases.append(("not_an_object", ["not", "a", "plan"], "schema_violation"))
    cases.append(
        ("unknown_dsl_version", _mutated(lambda p: p.update(dsl_version="v9")), "schema_violation")
    )
    cases.append(
        ("extra_unknown_field", _mutated(lambda p: p.update(evil="payload")), "schema_violation")
    )
    cases.append(
        (
            "unknown_step_type",
            _mutated(lambda p: p["steps"][1].update(type="write")),
            "schema_violation",
        )
    )

    def eleven_steps(p):
        p["steps"] = [copy.deepcopy(p["steps"][0]) for _ in range(11)]

    cases.append(("eleven_steps", _mutated(eleven_steps), "schema_violation"))
    cases.append(("missing_present", _mutated(lambda p: p["steps"].pop()), "present_misplaced"))

    def two_presents(p):
        extra = copy.deepcopy(p["steps"][2])
        extra["id"] = "s4"
        p["steps"].append(extra)

    cases.append(("duplicate_present", _mutated(two_presents), "present_misplaced"))

    def present_not_last(p):
        p["steps"][1], p["steps"][2] = p["steps"][2], p["steps"][1]
        p["steps"][1]["id"], p["steps"][2]["id"] = "s2", "s3"
        p["steps"][1]["input"] = "s1"
        p["steps"][2]["input"] = "s1"

    cases.append(("present_not_last", _mutated(present_not_last), "present_misplaced"))
    cases.append(
        ("forward_reference", _mutated(lambda p: p["steps"][1].update(input="s3")), "bad_reference")
    )
    cases.append(
        ("self_reference", _mutated(lambda p: p["steps"][1].update(input="s2")), "bad_reference")
    )
    cases.append(
        ("duplicate_ids", _mutated(lambda p: p["steps"][1].update(id="s1")), "step_id_sequence")
    )
    cases.append(
        (
            "star_on_sum",
            _mutated(lambda p: p["steps"][1]["ops"][0].update(op="sum")),
            "invalid_aggregate_column",
        )
    )
    cases.append(
        ("insert_template", _with_template("INSERT INTO orders VALUES (1)"), "template_not_select")
    )
    cases.append(
        ("select_into", _with_template("SELECT region INTO evil FROM orders"), "forbidden_keyword")
    )
    cases.append(
        (
            "multi_statement",
            _with_template("SELECT region FROM orders; DROP TABLE orders"),
            "multi_statement",
        )
    )
    cases.append(
        (
            "line_comment_smuggling",
            _with_template("SELECT region FROM orders -- WHERE 1=1"),
            "comment_forbidden",
        )
    )
    cases.append(
        (
            "block_comment_smuggling",
            _with_template("SELECT /*evil*/ region FROM orders"),
            "comment_forbidden",
        )
    )
    cases.append(
        (
            "brace_interpolation",
            _with_template("SELECT region FROM orders WHERE region = '{region}'"),
            "interpolation_forbidden",
        )
    )
    cases.append(
        (
            "stray_percent",
            _with_template("SELECT region FROM orders WHERE region LIKE '%north'"),
            "invalid_placeholder",
        )
    )
    cases.append(
        (
            "undeclared_placeholder",
            _with_template("SELECT region FROM orders WHERE region = %(region)s"),
            "undeclared_param",
        )
    )
    cases.append(
        (
            "orphan_declared_param",
            _with_template(
                "SELECT region FROM orders",
                {"ghost": {"type": "string", "value": "x"}},
            ),
            "unused_param",
        )
    )
    cases.append(
        (
            "forbidden_param_type",
            _with_template(
                "SELECT region FROM orders WHERE region = %(r)s",
                {"r": {"type": "list", "value": "x"}},
            ),
            "schema_violation",
        )
    )
    cases.append(
        (
            "sqli_string_in_int_param",
            _with_template(
                "SELECT region FROM orders WHERE id = %(order_id)s",
                {"order_id": {"type": "int", "value": "1 OR 1=1"}},
            ),
            "param_type_mismatch",
        )
    )
    cases.append(
        (
            "bool_smuggled_as_int",
            _with_template(
                "SELECT region FROM orders WHERE id = %(order_id)s",
                {"order_id": {"type": "int", "value": True}},
            ),
            "param_type_mismatch",
        )
    )
    cases.append(
        (
            "malformed_date_param",
            _with_template(
                "SELECT region FROM orders WHERE order_date >= %(since)s",
                {"since": {"type": "date", "value": "2026-13-99 OR 1=1"}},
            ),
            "param_type_mismatch",
        )
    )
    return cases


HOSTILE_CASES = _hostile_cases()


def test_hostile_case_count_meets_checkpoint():
    assert len(HOSTILE_CASES) >= 15


@pytest.mark.parametrize(
    ("name", "plan", "expected_code"),
    HOSTILE_CASES,
    ids=[name for name, _, _ in HOSTILE_CASES],
)
def test_hostile_plan_is_rejected(name, plan, expected_code):
    result = validate_plan(plan)
    assert result.valid is False
    assert result.errors, "rejection must carry a machine-readable reason"
    if expected_code is not None:
        assert expected_code in {e.code for e in result.errors}


def test_valid_plan_passes(valid_plan):
    result = validate_plan(valid_plan)
    assert result.valid is True
    assert result.errors == ()


def test_validator_never_raises_on_garbage():
    for garbage in (None, 42, "plan", b"bytes", {"steps": object()}):
        result = validate_plan(garbage)
        assert result.valid is False
