# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""JSON API v1: what the MCP server (an agent) can and cannot do."""

import json

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from boundary.approval_policy import ApprovalPolicy
from planner.client import PlannerClient
from tests.unit.conftest import VALID_PLAN
from tests.unit.test_api import CountingAdapter, _good_steps, _restricted_steps
from tests.unit.test_planner import SequencedLLM

ON = ApprovalPolicy(auto_approve_enabled=True)
BIG_STEPS = [
    VALID_PLAN["steps"][0],
    {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
]


def _client(tmp_path, *llm, policy=None, monkeypatch=None, token=None):
    if monkeypatch is not None and token is not None:
        monkeypatch.setenv("FONDACO_API_TOKEN", token)
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
        approval_policy=policy or ApprovalPolicy(),
    )
    return TestClient(app), adapter, tmp_path / "audit.jsonl"


def _events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_schema_is_the_planner_view(tmp_path):
    client, _, _ = _client(tmp_path)
    body = client.get("/api/v1/schema").json()
    assert body["clearance"] == "internal"
    tables = {t["name"]: t for t in body["tables"]}
    assert "orders" in tables and "label" in tables["orders"]


def test_planner_written_plan_waits_for_a_human(tmp_path):
    client, adapter, _ = _client(tmp_path, _good_steps())
    response = client.post("/api/v1/plans", json={"question": "orders per region?"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["policy"]["allow"] is True
    assert body["approval_path"] == f"/plans/{body['plan_id']}"
    executed = client.post(f"/api/v1/plans/{body['plan_id']}/execute")
    assert executed.status_code == 409
    assert adapter.execute_calls == 0
    # A human approves in the UI; the agent then reads the result.
    client.post(f"/plans/{body['plan_id']}/approve", data={"approver": "dana"})
    after = client.post(f"/api/v1/plans/{body['plan_id']}/execute").json()
    assert after["status"] == "executed"
    assert after["result"]["row_count"] == 2
    assert adapter.execute_calls == 1  # reading the result does not re-run it


def test_client_written_steps_cross_the_same_validator(tmp_path):
    client, _, path = _client(tmp_path)
    response = client.post("/api/v1/plans", json={"question": "q", "steps": BIG_STEPS})
    assert response.status_code == 201
    generated = [e for e in _events(path) if e["event"] == "plan_generated"][0]
    assert generated["payload"]["source"] == "client"


@pytest.mark.parametrize(
    "steps",
    [
        [{"id": "s1", "type": "query", "template": "DELETE FROM orders", "params": {}}],
        [{"id": "s1", "type": "query", "template": "SELECT 1; SELECT 2", "params": {}}],
        "not a list",
        [{"id": "s1", "type": "shell", "cmd": "rm -rf /"}],
    ],
)
def test_invalid_client_steps_are_refused_and_audited(tmp_path, steps):
    client, adapter, path = _client(tmp_path)
    response = client.post("/api/v1/plans", json={"question": "q", "steps": steps})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_plan"
    assert body["validation_errors"]
    events = [e["event"] for e in _events(path)]
    assert events == ["question_received", "plan_generated", "validation_result"]
    assert adapter.execute_calls == 0


def test_client_cannot_choose_the_plan_id(tmp_path):
    client, _, _ = _client(tmp_path)
    plan = {"question": "q", "steps": BIG_STEPS, "plan_id": "00000000-0000-4000-8000-000000000000"}
    body = client.post("/api/v1/plans", json=plan).json()
    assert body["plan_id"] != plan["plan_id"]


def test_denied_plan_is_final(tmp_path):
    client, adapter, _ = _client(tmp_path, _restricted_steps(), policy=ON)
    body = client.post("/api/v1/plans", json={"question": "cities?"}).json()
    assert body["status"] == "denied"
    assert client.post(f"/api/v1/plans/{body['plan_id']}/execute").status_code == 409
    assert adapter.execute_calls == 0


def test_auto_approved_plan_runs_only_when_executed(tmp_path):
    client, adapter, path = _client(tmp_path, policy=ON)
    body = client.post("/api/v1/plans", json={"question": "q", "steps": BIG_STEPS}).json()
    assert body["status"] == "auto_approved"
    assert adapter.execute_calls == 0
    result = client.post(f"/api/v1/plans/{body['plan_id']}/execute")
    assert result.status_code == 200
    assert result.json()["result"]["row_count"] == 12
    assert client.post(f"/api/v1/plans/{body['plan_id']}/execute").status_code == 200
    assert adapter.execute_calls == 1
    decisions = [e["payload"]["decision"] for e in _events(path) if e["event"] == "approval"]
    assert decisions == ["auto_approve"]


def test_small_auto_approved_result_is_held(tmp_path):
    client, adapter, _ = _client(tmp_path, _good_steps(), policy=ON)
    body = client.post("/api/v1/plans", json={"question": "q"}).json()
    held = client.post(f"/api/v1/plans/{body['plan_id']}/execute")
    assert held.status_code == 409
    assert held.json()["held_for_human"] is True
    assert held.json()["result"] is None
    assert "north" not in held.text


def test_there_is_no_approval_endpoint(tmp_path):
    client, _, _ = _client(tmp_path, _good_steps())
    plan_id = client.post("/api/v1/plans", json={"question": "q"}).json()["plan_id"]
    assert client.post(f"/api/v1/plans/{plan_id}/approve").status_code in (404, 405)


@pytest.mark.parametrize(
    ("payload", "status"),
    [({}, 400), ({"question": ""}, 400), ({"question": "x" * 2001}, 400), ([], 400)],
)
def test_bad_requests(tmp_path, payload, status):
    client, _, _ = _client(tmp_path)
    assert client.post("/api/v1/plans", json=payload).status_code == status


def test_oversized_body_and_unknown_plan(tmp_path):
    client, _, _ = _client(tmp_path)
    huge = {"question": "q", "steps": ["x" * 300_000]}
    assert client.post("/api/v1/plans", json=huge).status_code == 413
    assert client.get("/api/v1/plans/nope").status_code == 404


def test_bearer_token_when_configured(tmp_path, monkeypatch):
    client, _, _ = _client(tmp_path, monkeypatch=monkeypatch, token="s3cret-token")  # noqa: S106 — test value
    assert client.get("/api/v1/schema").status_code == 401
    assert client.post("/api/v1/plans", json={"question": "q"}).status_code == 401
    ok = client.get("/api/v1/schema", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code == 200
