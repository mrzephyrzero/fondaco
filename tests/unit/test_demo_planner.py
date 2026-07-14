# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Demo planner: fixtures validate through the shared boundary path, honestly."""

import pytest

from planner.client import PlannerError
from planner.demo import PROMPT_VERSION, SCRIPTED_QUESTIONS, DemoPlanner


@pytest.mark.parametrize("question", SCRIPTED_QUESTIONS, ids=range(1, len(SCRIPTED_QUESTIONS) + 1))
def test_every_scripted_question_yields_a_valid_plan(question):
    plan, trace = DemoPlanner().generate_plan(question, schema=None)
    # Built via the shared assemble_plan → validated by the real validator.
    assert trace.attempts[0].validation.valid is True
    assert plan["dsl_version"] == "v0"
    assert len(plan["plan_id"]) == 36  # boundary-assigned uuid, not a fixture constant
    assert plan["question"] == question


def test_trace_is_honest_about_being_a_fixture():
    _, trace = DemoPlanner().generate_plan(SCRIPTED_QUESTIONS[0], schema=None)
    assert trace.prompt_version == PROMPT_VERSION == "demo-fixtures"


def test_question_matching_is_normalized():
    # Different casing / spacing / trailing punctuation still resolves.
    q = "  how many PRODUCTS do we have per category  "
    plan, _ = DemoPlanner().generate_plan(q, schema=None)
    assert plan["steps"][0]["template"] == "SELECT category FROM products"


def test_unknown_question_fails_closed():
    with pytest.raises(PlannerError) as excinfo:
        DemoPlanner().generate_plan("what is the meaning of life?", schema=None)
    assert excinfo.value.code == "demo_unknown_question"


def test_deny_fixture_reads_restricted_customers():
    plan, _ = DemoPlanner().generate_plan(SCRIPTED_QUESTIONS[9], schema=None)
    assert "customers" in plan["steps"][0]["template"]


def test_plan_ids_are_unique_per_call():
    p1, _ = DemoPlanner().generate_plan(SCRIPTED_QUESTIONS[0], schema=None)
    p2, _ = DemoPlanner().generate_plan(SCRIPTED_QUESTIONS[0], schema=None)
    assert p1["plan_id"] != p2["plan_id"]
