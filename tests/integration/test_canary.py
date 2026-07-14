# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Checkpoint P3: canary test — planted row values never leave the perimeter.

Runs the complete ask-flow (schema → planner → validation → policy →
execution) with a mocked LLM whose transport captures every outbound
byte, including a forced repair round. Canary strings live in *data
rows* only; if any outbound request contains one, the boundary leaked.
"""

import json

import httpx
import pytest

from tests.integration.conftest import requires_db

pytestmark = requires_db

CANARIES = (
    "CANARY-7f3e2a91-name",
    "canary-secret@leak.test",
    "CANARY-CITY-XANADU",
)


@pytest.fixture(scope="module")
def planted_canaries(seeded_database):
    import psycopg

    from tests.integration.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO customers (name, email, phone, city) VALUES (%s, %s, %s, %s)",
            (CANARIES[0], CANARIES[1], "+00 000", CANARIES[2]),
        )
    yield CANARIES
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM customers WHERE name = %s", (CANARIES[0],))


def test_canaries_never_in_outbound_llm_traffic(adapter, planted_canaries):
    from boundary.policy import evaluate
    from executor.adapters.contract import schema_labels_dict
    from executor.runner import run_plan
    from planner.client import PlannerClient
    from tests.unit.conftest import VALID_PLAN

    captured: list[str] = []
    bad_then_good = [
        # First reply is invalid (forces a repair round with validation errors),
        # second is a valid plan — both directions captured.
        json.dumps({"steps": [{"id": "s1", "type": "query", "template": "DROP x", "params": {}}]}),
        json.dumps({"steps": VALID_PLAN["steps"]}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.content.decode("utf-8"))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": bad_then_good.pop(0)}}]}
        )

    client = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=httpx.MockTransport(handler),
    )

    schema = adapter.get_schema()
    plan, trace = client.generate_plan("How many orders per region since 2026-01-01?", schema)
    decision = evaluate(plan, schema_labels_dict(schema), "internal")
    assert decision.allow is True
    result = run_plan(plan, adapter)
    assert result.label == "internal"

    # The actual assertion of the phase: nothing that crossed the wire
    # contains a planted value — not the schema payload, not the question,
    # not the repair-round error feedback.
    assert len(captured) == 2, "expected initial + repair requests"
    for body in captured:
        for canary in planted_canaries:
            assert canary not in body


def test_schema_payload_contains_no_sample_values(adapter, planted_canaries):
    from planner.client import _schema_payload

    payload = _schema_payload(adapter.get_schema())
    for canary in planted_canaries:
        assert canary not in payload
