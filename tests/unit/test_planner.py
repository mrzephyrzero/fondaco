# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Planner client: envelope ownership, strict parsing, fail-closed repair loop."""

import json

import httpx
import pytest

from executor.adapters.contract import AnnotatedSchema, Column, Table
from planner.client import PlannerClient, PlannerError
from tests.unit.conftest import VALID_PLAN

SCHEMA = AnnotatedSchema(
    tables=(
        Table(
            name="orders",
            label="internal",
            columns=(
                Column(name="region", sql_type="text", label="internal"),
                Column(name="status", sql_type="text", label="internal"),
                Column(name="order_date", sql_type="date", label="internal"),
            ),
            comment="Customer orders",
            row_count=20000,
        ),
    )
)


class SequencedLLM:
    """Mock transport: returns canned completion texts one per request."""

    def __init__(self, *texts: str):
        self._texts = list(texts)
        self.request_bodies: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.request_bodies.append(request.content.decode("utf-8"))
            text = self._texts.pop(0)
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

        return httpx.MockTransport(handler)


def _client(llm: SequencedLLM, max_attempts: int = 2) -> PlannerClient:
    return PlannerClient(
        base_url="https://llm.invalid/v1",
        api_key="test-key",  # noqa: S106 — dummy for the mock transport
        model="test-model",
        max_attempts=max_attempts,
        transport=llm.transport(),
    )


def _steps_json(extra: dict | None = None) -> str:
    payload = {"steps": VALID_PLAN["steps"], **(extra or {})}
    return json.dumps(payload)


def test_happy_path_builds_envelope_boundary_side():
    llm = SequencedLLM(_steps_json({"plan_id": "model-chosen-id", "evil": True}))
    plan, trace = _client(llm).generate_plan("How many orders per region?", SCHEMA)
    assert plan["dsl_version"] == "v0"
    assert plan["question"] == "How many orders per region?"
    assert plan["plan_id"] != "model-chosen-id"  # boundary-assigned uuid, never the model's
    assert len(plan["plan_id"]) == 36
    assert plan["steps"] == VALID_PLAN["steps"]
    assert len(trace.attempts) == 1


def test_fenced_json_is_tolerated():
    llm = SequencedLLM(f"```json\n{_steps_json()}\n```")
    plan, _ = _client(llm).generate_plan("q?", SCHEMA)
    assert plan["steps"] == VALID_PLAN["steps"]


def test_prose_then_valid_uses_second_attempt():
    llm = SequencedLLM("Sure! Here is my thinking...", _steps_json())
    plan, trace = _client(llm).generate_plan("q?", SCHEMA)
    assert plan["steps"] == VALID_PLAN["steps"]
    assert len(trace.attempts) == 2
    assert trace.attempts[0].parse_error is not None


def test_repair_feedback_is_errors_only():
    bad_steps = json.dumps(
        {
            "steps": [
                {"id": "s1", "type": "query", "template": "DELETE FROM orders", "params": {}},
                {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
            ]
        }
    )
    llm = SequencedLLM(bad_steps, _steps_json())
    plan, trace = _client(llm).generate_plan("q?", SCHEMA)
    assert plan["steps"] == VALID_PLAN["steps"]
    repair_request = llm.request_bodies[1]
    assert "template_not_select" in repair_request  # machine-readable error code fed back


def test_attempts_exhausted_fails_closed():
    bad = json.dumps({"steps": [{"id": "s1", "type": "write", "template": "x"}]})
    llm = SequencedLLM(bad, bad)
    with pytest.raises(PlannerError) as excinfo:
        _client(llm).generate_plan("q?", SCHEMA)
    assert excinfo.value.code == "attempts_exhausted"
    assert len(llm.request_bodies) == 2  # hard cap honored


def test_outbound_payload_is_schema_and_question_only():
    llm = SequencedLLM(_steps_json())
    _client(llm).generate_plan("How many orders per region?", SCHEMA)
    body = json.loads(llm.request_bodies[0])
    assert body["temperature"] == 0
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user"]
    user_content = body["messages"][1]["content"]
    assert "SCHEMA:" in user_content and "QUESTION:" in user_content
    assert "row_count" in user_content  # statistics yes
    assert "20000" in user_content


def test_llm_unreachable_fails_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="m",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(PlannerError) as excinfo:
        client.generate_plan("q?", SCHEMA)
    assert excinfo.value.code == "llm_unreachable"
