# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Risk-proportional approval: off by default, every criterion required, fail closed."""

import json

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from boundary.approval_policy import (
    AUTO_APPROVED,
    PENDING_APPROVAL,
    ApprovalPolicy,
    decide,
    policy_from_env,
    release_allowed,
)
from boundary.policy import Label, evaluate
from planner.client import PlannerClient
from tests.unit.conftest import VALID_PLAN
from tests.unit.test_api import CountingAdapter, _ask, _good_steps, _restricted_steps
from tests.unit.test_planner import SequencedLLM

LABELS = {
    "orders": {
        "label": "internal",
        "columns": {"region": "internal", "status": "internal", "order_date": "internal"},
    },
    "products": {"label": "public", "columns": {"category": "public"}},
    "customers": {"label": "restricted", "columns": {"city": "confidential"}},
    "invoices": {"label": "internal", "columns": {"margin": "confidential"}},
}
ON = ApprovalPolicy(auto_approve_enabled=True)


def _plan(template: str = "SELECT region FROM orders") -> dict:
    return {
        "dsl_version": "v0",
        "plan_id": VALID_PLAN["plan_id"],
        "question": "q",
        "steps": [
            {"id": "s1", "type": "query", "template": template, "params": {}},
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
        ],
    }


def _decide(plan, policy=ON, clearance="confidential", budget=20):
    return decide(policy, plan, evaluate(plan, LABELS, clearance), LABELS, budget)


# ── The decision ──────────────────────────────────────────────────────────


def test_disabled_by_default():
    assert ApprovalPolicy().auto_approve_enabled is False
    assert _decide(_plan(), policy=ApprovalPolicy()).status == PENDING_APPROVAL
    assert policy_from_env({}).auto_approve_enabled is False


def test_low_risk_plan_is_auto_approved():
    verdict = _decide(_plan())
    assert verdict.status == AUTO_APPROVED
    assert verdict.auto is True


def test_policy_deny_is_never_auto_approved():
    plan = _plan("SELECT city FROM customers")
    verdict = _decide(plan, clearance="internal")
    assert verdict.status == PENDING_APPROVAL
    assert verdict.reasons == ("policy_denied",)


def test_label_above_max_needs_a_human():
    # Allowed at confidential clearance, but above the internal auto-approve cap.
    verdict = _decide(_plan("SELECT margin FROM invoices"))
    assert verdict.status == PENDING_APPROVAL
    assert "label_above_auto_approve_max" in verdict.reasons


def test_public_cap_holds_back_internal():
    policy = ApprovalPolicy(True, Label.PUBLIC)
    assert _decide(_plan(), policy=policy).status == PENDING_APPROVAL
    assert _decide(_plan("SELECT category FROM products"), policy=policy).auto is True


def test_unresolved_reference_needs_a_human():
    # Allowed at restricted clearance only because unresolved → restricted; but
    # even with a restricted cap it is never auto-approved.
    policy = ApprovalPolicy(True, Label.CONFIDENTIAL)
    plan = _plan("SELECT nosuch FROM orders")
    verdict = decide(policy, plan, evaluate(plan, LABELS, "restricted"), LABELS, 20)
    assert verdict.status == PENDING_APPROVAL
    assert any(r.startswith("unresolved_reference") for r in verdict.reasons)


def test_insufficient_budget_needs_a_human():
    verdict = _decide(_plan(), budget=0)
    assert verdict.status == PENDING_APPROVAL
    assert "budget_insufficient" in verdict.reasons


def test_internal_fault_needs_a_human():
    verdict = decide(ON, {"steps": None}, evaluate(_plan(), LABELS, "internal"), LABELS, 20)
    assert verdict.status == PENDING_APPROVAL
    assert verdict.reasons[0].startswith("approval_policy_error")


# ── Configuration ────────────────────────────────────────────────────────


def test_restricted_can_never_be_the_cap():
    assert ApprovalPolicy(True, Label.RESTRICTED).auto_approve_max_label == Label.INTERNAL
    env = {"FONDACO_AUTO_APPROVE_ENABLED": "true", "FONDACO_AUTO_APPROVE_MAX_LABEL": "restricted"}
    assert policy_from_env(env).auto_approve_max_label == Label.INTERNAL


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"FONDACO_AUTO_APPROVE_ENABLED": "true"}, (True, Label.INTERNAL, 10)),
        ({"FONDACO_AUTO_APPROVE_ENABLED": "maybe"}, (False, Label.INTERNAL, 10)),
        (
            {
                "FONDACO_AUTO_APPROVE_ENABLED": "1",
                "FONDACO_AUTO_APPROVE_MAX_LABEL": "confidential",
                "FONDACO_AUTO_APPROVE_MIN_ROWS": "25",
            },
            (True, Label.CONFIDENTIAL, 25),
        ),
        (
            {"FONDACO_AUTO_APPROVE_MAX_LABEL": "secret", "FONDACO_AUTO_APPROVE_MIN_ROWS": "x"},
            (False, Label.INTERNAL, 10),
        ),
        ({"FONDACO_AUTO_APPROVE_MIN_ROWS": "0"}, (False, Label.INTERNAL, 10)),
        ({"FONDACO_AUTO_APPROVE_MIN_ROWS": "-5"}, (False, Label.INTERNAL, 10)),
    ],
)
def test_policy_from_env(env, expected):
    policy = policy_from_env(env)
    actual = (
        policy.auto_approve_enabled,
        policy.auto_approve_max_label,
        policy.auto_approve_min_rows,
    )
    assert actual == expected


def test_hostile_construction_falls_back():
    policy = ApprovalPolicy("yes", "public", True)  # type: ignore[arg-type]
    assert policy.auto_approve_enabled is False
    assert policy.auto_approve_max_label == Label.INTERNAL
    assert policy.auto_approve_min_rows == 10


def test_release_threshold():
    assert release_allowed(ON, 10) is True
    assert release_allowed(ON, 9) is False
    assert release_allowed(ON, True) is False
    assert release_allowed(ON, None) is False


# ── Through the API ───────────────────────────────────────────────────────


def _client(tmp_path, *llm, policy=ON):
    adapter = CountingAdapter()
    planner = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=SequencedLLM(*llm).transport(),
    )
    app = create_app(
        adapter=adapter,
        planner=planner,
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
        approval_policy=policy,
    )
    return TestClient(app), adapter, tmp_path / "audit.jsonl"


def _entries(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _single_query_steps() -> str:
    # 12 raw rows from CountingAdapter, presented directly: above min_rows=10.
    return json.dumps(
        {
            "steps": [
                VALID_PLAN["steps"][0],
                {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
            ]
        }
    )


def test_auto_approved_plan_executes_without_a_click_and_is_audited(tmp_path):
    client, adapter, path = _client(tmp_path, _single_query_steps())
    plan_id = _ask(client)
    assert adapter.execute_calls == 1
    page = client.get(f"/plans/{plan_id}")
    assert "auto-approved by risk policy" in page.text
    assert "executed" in page.text
    approvals = [e for e in _entries(path) if e["event"] == "approval"]
    assert approvals[0]["actor"] == "auto-approval-policy"
    assert approvals[0]["payload"]["decision"] == "auto_approve"
    assert any(e["event"] == "execution_digest" for e in _entries(path))


def test_small_result_is_held_until_a_human_releases_it(tmp_path):
    # The aggregate yields 2 groups — below min_rows=10.
    client, adapter, path = _client(tmp_path, _good_steps())
    plan_id = _ask(client)
    assert adapter.execute_calls == 1
    page = client.get(f"/plans/{plan_id}")
    assert "held until a human releases it" in page.text
    assert "north" not in page.text  # the held rows are not shown
    decisions = [e["payload"]["decision"] for e in _entries(path) if e["event"] == "approval"]
    assert decisions == ["auto_approve", "hold_for_human"]

    client.post(f"/plans/{plan_id}/approve", data={"approver": "dana"})
    assert adapter.execute_calls == 1  # released, not re-run
    assert "north" in client.get(f"/plans/{plan_id}").text


def test_denied_plan_never_reaches_the_approval_policy(tmp_path):
    client, adapter, path = _client(tmp_path, _restricted_steps())
    _ask(client)
    assert adapter.execute_calls == 0
    assert not [e for e in _entries(path) if e["event"] == "approval"]


def test_disabled_policy_keeps_the_v1_flow_byte_for_byte(tmp_path):
    client, adapter, path = _client(tmp_path, _good_steps(), policy=ApprovalPolicy())
    plan_id = _ask(client)
    assert adapter.execute_calls == 0
    assert [e["event"] for e in _entries(path)] == [
        "question_received",
        "plan_generated",
        "validation_result",
        "policy_decision",
    ]
    client.post(f"/plans/{plan_id}/approve", data={"approver": "dana"})
    assert adapter.execute_calls == 1
