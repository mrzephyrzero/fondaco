# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Full API loop against real Postgres (mocked LLM): labels match Phase 2."""

import json

from tests.integration.conftest import requires_db

pytestmark = requires_db

PLAN_STEPS = json.dumps(
    {
        "steps": [
            {
                "id": "s1",
                "type": "query",
                "template": (
                    "SELECT region, total_amount FROM orders WHERE order_date >= %(since)s"
                ),
                "params": {"since": {"type": "date", "value": "2025-10-01"}},
            },
            {
                "id": "s2",
                "type": "aggregate",
                "input": "s1",
                "group_by": ["region"],
                "ops": [{"op": "count", "column": "*", "as": "n_orders"}],
            },
            {"id": "s3", "type": "present", "input": "s2", "format": "table", "title": "Orders"},
        ]
    }
)


def test_full_loop_against_postgres(adapter, tmp_path):
    from fastapi.testclient import TestClient

    from api.main import create_app
    from planner.client import PlannerClient
    from tests.unit.test_planner import SequencedLLM

    llm = SequencedLLM(PLAN_STEPS)
    app = create_app(
        adapter=adapter,
        planner=PlannerClient(
            base_url="https://llm.invalid/v1", model="mock", transport=llm.transport()
        ),
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
    )
    client = TestClient(app)

    response = client.post("/ask", data={"question": "Orders per region since October 2025?"})
    plan_id = str(response.url).rsplit("/plans/", 1)[1]
    assert "ALLOWED" in response.text

    done = client.post(f"/plans/{plan_id}/approve", data={"approver": "integration"})
    assert done.status_code == 200
    assert "executed" in done.text
    assert 'class="badge label-internal"' in done.text  # real label from real data

    audit_page = client.get("/audit", params={"plan": plan_id})
    assert "execution_digest" in audit_page.text
