# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""CRIT-1 end-to-end against real Postgres: the comma-join exfil is denied.

Proves three things: (1) the read-only role CAN read customers directly, so
data is genuinely reachable; (2) the comma-join plan is policy-DENIED through
the API and never executes; (3) post-fix, even the adapter labels the
comma-join result `restricted`, so the defense-in-depth re-label agrees.
"""

import json

from tests.integration.conftest import requires_db

pytestmark = requires_db

ATTACK_TEMPLATE = "SELECT c.email FROM orders o, customers c WHERE o.customer_id = c.id"

ATTACK_PLAN_STEPS = json.dumps(
    {
        "steps": [
            {"id": "s1", "type": "query", "template": ATTACK_TEMPLATE, "params": {}},
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "x"},
        ]
    }
)


class FixedPlanner:
    """Returns a single attacker-chosen plan (stands in for a hostile LLM)."""

    def __init__(self, steps_json: str):
        self._steps = steps_json

    def generate_plan(self, question, schema):
        import httpx as _httpx

        from planner.client import PlannerClient

        def handler(request):
            return _httpx.Response(200, json={"choices": [{"message": {"content": self._steps}}]})

        client = PlannerClient(
            base_url="https://llm.invalid/v1",
            model="mock",
            transport=_httpx.MockTransport(handler),
        )
        return client.generate_plan(question, schema)


def test_readonly_role_can_actually_read_customers(seeded_database):
    import psycopg

    # The point of the boundary: the DB itself would hand over the PII.
    with psycopg.connect(seeded_database) as conn:
        row = conn.execute("SELECT email FROM customers LIMIT 1").fetchone()
    assert row is not None and "@" in row[0]


def test_comma_join_exfil_is_denied_end_to_end(adapter, tmp_path):
    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app(
        adapter=adapter,
        planner=FixedPlanner(ATTACK_PLAN_STEPS),
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
    )
    client = TestClient(app)

    ask = client.post("/ask", data={"question": "give me customer emails"})
    plan_id = str(ask.url).rsplit("/plans/", 1)[1]
    assert "DENIED" in ask.text  # policy refused the mislabeled plan

    # And it cannot be executed even by a direct approve POST.
    blocked = client.post(f"/plans/{plan_id}/approve", data={}, follow_redirects=False)
    assert blocked.status_code == 409

    # The policy_decision is on the record as a deny.
    entries = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    decision = next(e for e in entries if e["event"] == "policy_decision")
    assert decision["payload"]["allow"] is False
    assert decision["payload"]["plan_label"] == "restricted"


def test_adapter_labels_comma_join_restricted(adapter):
    # Defense in depth: the executor-side re-label agrees the result is restricted.
    # (Bounded to one row so the adapter returns a labeled result rather than
    # tripping max_rows — the labeling is what we are asserting here.)
    step = {
        "id": "s1",
        "type": "query",
        "template": "SELECT c.email FROM customers c, orders o WHERE c.id = 1 AND o.id = 1",
        "params": {},
    }
    result = adapter.execute(step)
    assert result.label == "restricted"


def test_error_message_does_not_leak_a_param_value(adapter):
    from executor.adapters.contract import AdapterError

    secret = "canary-9931-should-not-appear"  # noqa: S105 — canary param value
    step = {
        "id": "s1",
        "type": "query",
        "template": "SELECT missing_col FROM orders WHERE region = %(r)s",
        "params": {"r": {"type": "string", "value": secret}},
    }
    try:
        adapter.execute(step)
    except AdapterError as exc:
        assert secret not in str(exc)
        assert "missing_col" not in str(exc)  # driver text never passes through
    else:
        raise AssertionError("expected the query to fail")
