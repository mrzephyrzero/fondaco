# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Demo scenarios end-to-end through the REAL boundary (keyless, CI-friendly).

Drives all ten fixture questions through create_app with the DemoPlanner —
same validator, policy, executor, guards, and audit as the live path. No LLM
key needed, so this runs in CI and also guards the fixtures against dataset
drift.
"""

import pytest

from tests.integration.conftest import requires_db

pytestmark = requires_db

# (question index, expected label) for the nine answerable scenarios.
ANSWERABLE = [
    (0, "internal"),
    (1, "internal"),
    (2, "internal"),
    (3, "internal"),
    (4, "public"),
    (5, "internal"),
    (6, "internal"),
    (7, "internal"),
    (8, "internal"),
]


def _client(adapter, tmp_path):
    from fastapi.testclient import TestClient

    from api.main import create_app
    from planner.demo import DemoPlanner

    app = create_app(
        adapter=adapter,
        planner=DemoPlanner(),
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
    )
    return TestClient(app)


def _ask(client, question):
    response = client.post("/ask", data={"question": question})
    return str(response.url).rsplit("/plans/", 1)[1], response


@pytest.mark.parametrize(("index", "label"), ANSWERABLE, ids=[str(i + 1) for i, _ in ANSWERABLE])
def test_answerable_scenarios_execute_with_expected_label(adapter, tmp_path, index, label):
    from planner.demo import SCRIPTED_QUESTIONS

    client = _client(adapter, tmp_path)
    plan_id, ask_response = _ask(client, SCRIPTED_QUESTIONS[index])
    assert "ALLOWED" in ask_response.text

    done = client.post(f"/plans/{plan_id}/approve", data={"approver": "demo"})
    assert done.status_code == 200
    assert "executed" in done.text
    assert f'class="badge label-{label}"' in done.text


def test_demo_banner_is_shown(adapter, tmp_path):
    client = _client(adapter, tmp_path)
    page = client.get("/")
    assert "DEMO MODE" in page.text
    assert "no LLM is involved" in page.text


def test_pii_scenario_is_policy_denied(adapter, tmp_path):
    from planner.demo import SCRIPTED_QUESTIONS

    client = _client(adapter, tmp_path)
    plan_id, ask_response = _ask(client, SCRIPTED_QUESTIONS[9])  # customers in Venezia
    assert "DENIED" in ask_response.text

    # A denied plan cannot be executed, even by direct POST.
    blocked = client.post(
        f"/plans/{plan_id}/approve", data={"approver": "x"}, follow_redirects=False
    )
    assert blocked.status_code == 409


def test_full_audit_chain_for_a_demo_request(adapter, tmp_path):
    import json

    from boundary.audit import AuditLog
    from planner.demo import SCRIPTED_QUESTIONS

    client = _client(adapter, tmp_path)
    plan_id, _ = _ask(client, SCRIPTED_QUESTIONS[0])
    client.post(f"/plans/{plan_id}/approve", data={"approver": "demo"})

    audit_path = tmp_path / "audit.jsonl"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    # The plan_generated entry is honest about the fixture provenance.
    generated = next(e for e in events if e["event"] == "plan_generated")
    assert generated["payload"]["prompt_version"] == "demo-fixtures"
    assert AuditLog(audit_path).verify().ok is True
