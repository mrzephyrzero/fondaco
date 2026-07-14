# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""API loop: ask → inspect → approve/reject, fully audited, fail closed.

Checkpoint P4: a rejected plan is provably never executed — the fake
adapter counts execute() calls.
"""

import json

from fastapi.testclient import TestClient

from api.main import create_app
from executor.adapters.contract import (
    AnnotatedSchema,
    Capabilities,
    Column,
    LabeledResult,
    Table,
)
from planner.client import PlannerClient
from tests.unit.conftest import VALID_PLAN
from tests.unit.test_planner import SequencedLLM

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
            row_count=20000,
        ),
        Table(
            name="customers",
            label="restricted",
            columns=(Column(name="city", sql_type="text", label="confidential"),),
            row_count=1000,
        ),
    )
)


class CountingAdapter:
    """Fake adapter that counts executions — the rejected-plan proof."""

    def __init__(self):
        self.execute_calls = 0

    def get_schema(self) -> AnnotatedSchema:
        return SCHEMA

    def execute(self, step):
        self.execute_calls += 1
        rows = (("north", "paid"), ("south", "pending"))
        return LabeledResult(
            columns=("region", "status"),
            rows=rows,
            label="internal",
            row_count=len(rows),
            digest="d" * 64,
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dsl_versions=("v0",),
            param_types=("string", "int", "float", "bool", "date", "timestamp"),
            max_rows=10_000,
            read_only=True,
        )


def _mk(tmp_path, *llm_texts):
    llm = SequencedLLM(*llm_texts)
    adapter = CountingAdapter()
    planner = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=llm.transport(),
    )
    app = create_app(
        adapter=adapter,
        planner=planner,
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
    )
    return TestClient(app), adapter, tmp_path / "audit.jsonl"


def _good_steps() -> str:
    return json.dumps({"steps": VALID_PLAN["steps"]})


def _restricted_steps() -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "id": "s1",
                    "type": "query",
                    "template": "SELECT city FROM customers",
                    "params": {},
                },
                {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
            ]
        }
    )


def _ask(client, question="How many orders per region?"):
    response = client.post("/ask", data={"question": question})
    assert response.status_code == 200
    assert "/plans/" in str(response.url)
    return str(response.url).rsplit("/plans/", 1)[1]


def test_full_loop_ask_approve_execute_audited(tmp_path):
    client, adapter, audit_path = _mk(tmp_path, _good_steps())
    plan_id = _ask(client)

    page = client.get(f"/plans/{plan_id}")
    assert "ALLOWED" in page.text and "pending" in page.text
    assert adapter.execute_calls == 0  # inspection executes nothing

    done = client.post(f"/plans/{plan_id}/approve", data={"approver": "alice"})
    assert done.status_code == 200
    assert adapter.execute_calls == 1
    assert "executed" in done.text and "internal" in done.text and "alice" in done.text

    events = [json.loads(line)["event"] for line in audit_path.read_text().splitlines()]
    assert set(events) == {
        "question_received",
        "plan_generated",
        "validation_result",
        "policy_decision",
        "approval",
        "execution_digest",
    }
    from boundary.audit import AuditLog

    assert AuditLog(audit_path).verify().ok is True


def test_rejected_plan_is_provably_never_executed(tmp_path):
    client, adapter, audit_path = _mk(tmp_path, _good_steps())
    plan_id = _ask(client)

    rejected = client.post(f"/plans/{plan_id}/reject", data={"approver": "bob"})
    assert rejected.status_code == 200
    assert adapter.execute_calls == 0

    late_approve = client.post(
        f"/plans/{plan_id}/approve", data={"approver": "mallory"}, follow_redirects=False
    )
    assert late_approve.status_code == 409
    assert adapter.execute_calls == 0  # the checkpoint proof

    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    approvals = [e for e in entries if e["event"] == "approval"]
    assert len(approvals) == 1 and approvals[0]["payload"]["decision"] == "reject"
    assert not any(e["event"] == "execution_digest" for e in entries)


def test_policy_denied_plan_cannot_be_approved(tmp_path):
    client, adapter, _ = _mk(tmp_path, _restricted_steps())
    plan_id = _ask(client, "List customer cities")

    page = client.get(f"/plans/{plan_id}")
    assert "DENIED" in page.text
    assert "Approve" not in page.text  # no approve button rendered

    response = client.post(
        f"/plans/{plan_id}/approve", data={"approver": "mallory"}, follow_redirects=False
    )
    assert response.status_code == 409
    assert adapter.execute_calls == 0


def test_planner_failure_is_audited_and_nothing_pends(tmp_path):
    client, adapter, audit_path = _mk(tmp_path, "not json", "still not json")
    response = client.post("/ask", data={"question": "anything"}, follow_redirects=False)
    assert response.status_code == 303
    assert "error=planner" in response.headers["location"]
    assert adapter.execute_calls == 0

    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    generated = [e for e in entries if e["event"] == "plan_generated"]
    assert len(generated) == 1 and generated[0]["payload"]["success"] is False


def test_unknown_plan_404_and_double_approve_409(tmp_path):
    client, adapter, _ = _mk(tmp_path, _good_steps())
    assert client.get("/plans/nope").status_code == 404

    plan_id = _ask(client)
    assert client.post(f"/plans/{plan_id}/approve", data={}).status_code == 200
    second = client.post(f"/plans/{plan_id}/approve", data={}, follow_redirects=False)
    assert second.status_code == 409
    assert adapter.execute_calls == 1


def test_audit_view_shows_chain_and_filters(tmp_path):
    client, _, _ = _mk(tmp_path, _good_steps())
    plan_id = _ask(client)
    client.post(f"/plans/{plan_id}/approve", data={"approver": "alice"})

    page = client.get("/audit")
    assert "hash chain verified" in page.text
    assert plan_id in page.text

    # The dropdown lists every event name; assert on rendered table cells instead.
    filtered = client.get("/audit", params={"event": "approval"})
    assert "<code>approval</code>" in filtered.text
    assert "<code>question_received</code>" not in filtered.text
