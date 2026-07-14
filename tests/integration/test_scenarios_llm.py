# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Checkpoint P3: live-LLM scenario run — ≥ 8/10 valid, policy-passing plans.

Needs both a database (dataset) and a real LLM endpoint; skipped without
FONDACO_LLM_API_KEY (or a local base URL like Ollama). Never run in CI.
"""

import os

import pytest

from tests.integration.conftest import requires_db

LLM_CONFIGURED = bool(os.environ.get("FONDACO_LLM_API_KEY")) or "localhost" in os.environ.get(
    "FONDACO_LLM_BASE_URL", ""
)

pytestmark = [
    requires_db,
    pytest.mark.skipif(not LLM_CONFIGURED, reason="FONDACO_LLM_* not configured"),
]

CLEARANCE = os.environ.get("FONDACO_EGRESS_CLEARANCE", "internal")

SCENARIOS = [
    ("Q1", "How many orders were placed per region since October 2025?", "pass"),
    ("Q2", "What is the total revenue per region for orders placed in 2026?", "pass"),
    ("Q3", "What was the average order value per order status in 2026?", "pass"),
    ("Q4", "How many deliveries did each carrier handle in 2026?", "pass"),
    ("Q5", "How many products do we have per category?", "pass"),
    ("Q6", "What is the highest single order amount recorded in 2026?", "pass"),
    (
        "Q7",
        "What total quantity moved out of each warehouse in 2026 (outbound movements only)?",
        "pass",
    ),
    ("Q8", "How many orders were cancelled per region in 2026?", "pass"),
    ("Q9", "How many orders were placed per month in the first half of 2026?", "pass"),
    ("Q10", "List the names and emails of customers in Venezia.", "deny"),
]


def test_scenarios_meet_checkpoint(adapter):
    from boundary.policy import evaluate
    from executor.adapters.contract import schema_labels_dict
    from executor.runner import ExecutorError, run_plan
    from planner.client import PlannerError, client_from_env

    client = client_from_env()
    schema = adapter.get_schema()
    schema_labels = schema_labels_dict(schema)

    outcomes: list[tuple[str, str, str]] = []
    passing = 0
    denied_as_expected = 0
    for qid, question, expected in SCENARIOS:
        try:
            plan, trace = client.generate_plan(question, schema)
        except PlannerError as exc:
            outcomes.append((qid, expected, f"planner_failed:{exc.code}"))
            continue
        decision = evaluate(plan, schema_labels, CLEARANCE)
        if not decision.allow:
            outcomes.append((qid, expected, f"policy_deny:{decision.reason_code}"))
            if expected == "deny":
                denied_as_expected += 1
            continue
        try:
            result = run_plan(plan, adapter)
        except ExecutorError as exc:
            # Valid + policy-passing but not executable (e.g. SQL function the
            # backend lacks). Stricter than the checkpoint requires, but a plan
            # that cannot run should not count as a pass.
            outcomes.append((qid, expected, f"execution_failed:{exc.code}"))
            continue
        outcomes.append((qid, expected, f"ok:label={result.label},rows={len(result.rows)}"))
        if expected == "pass":
            passing += 1

    report = "\n".join(f"{qid:4} expected={exp:5} got={got}" for qid, exp, got in outcomes)
    print(f"\n=== Scenario outcomes (clearance={CLEARANCE}) ===\n{report}")

    assert passing >= 8, f"checkpoint requires >= 8 passing scenarios\n{report}"
    assert denied_as_expected == 1, f"Q10 must be policy-denied\n{report}"
