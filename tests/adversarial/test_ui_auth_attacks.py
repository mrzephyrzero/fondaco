# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Threat 25: an agent (or a hostile page) drives the approval UI.

The attacker can reach the boundary over HTTP — it may even hold the
`/api/v1` bearer token, as the MCP server does — but holds no approver
token. Nothing it sends may approve, reject, ask, read a plan page or the
audit view, and no attempt may reach the database.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from api.auth import UI_SESSION_COOKIE
from api.main import create_app
from boundary.audit import FileAnchorSink
from planner.client import PlannerClient
from tests.ui_auth import approver_token, sign_in
from tests.unit.test_api import CountingAdapter, _ask, _good_steps
from tests.unit.test_planner import SequencedLLM

AGENT_TOKEN = "agent-only-api-token-0123456789abcdef"  # noqa: S105 - test-only


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDACO_API_TOKEN", AGENT_TOKEN)
    adapter = CountingAdapter()
    planner = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=SequencedLLM(*[_good_steps()] * 6).transport(),
    )
    app = create_app(
        adapter=adapter,
        planner=planner,
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
        anchor_sink=FileAnchorSink(tmp_path / "anchors.jsonl"),
    )
    human = sign_in(TestClient(app), "alice")
    plan_id = _ask(human)
    agent = TestClient(app)
    return app, human, agent, adapter, plan_id, tmp_path / "audit.jsonl"


def _approvals(path):
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if json.loads(line)["event"] == "approval"
    ]


@pytest.mark.parametrize("path", ["/", "/audit", "/audit?event=approval", "/plans/{plan_id}"])
def test_unauthenticated_reads_redirect_to_login_and_leak_nothing(setup, path):
    _, _, agent, _, plan_id, _ = setup
    response = agent.get(path.format(plan_id=plan_id), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert plan_id not in response.text


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_agent_without_approver_token_cannot_decide(setup, action):
    _, _, agent, adapter, plan_id, audit_path = setup
    response = agent.post(
        f"/plans/{plan_id}/{action}", data={"approver": "alice"}, follow_redirects=False
    )
    assert response.status_code == 401
    assert adapter.execute_calls == 0
    assert _approvals(audit_path) == []


def test_unauthenticated_ask_is_refused_before_the_audit(setup):
    _, _, agent, _, _, audit_path = setup
    before = audit_path.read_text()
    assert agent.post("/ask", data={"question": "orders?"}).status_code == 401
    assert agent.post("/ask", json={"question": "orders?"}).status_code == 401
    assert audit_path.read_text() == before


def test_api_bearer_token_does_not_open_the_ui(setup):
    _, _, agent, adapter, plan_id, audit_path = setup
    agent.headers["Authorization"] = f"Bearer {AGENT_TOKEN}"
    # The token is good for /api/v1 …
    proposed = agent.post("/api/v1/plans", json={"question": "orders per region?"})
    assert proposed.status_code == 201
    agent_plan = proposed.json()["plan_id"]
    # … and for nothing in the UI, including approving its own plan.
    for pid in (plan_id, agent_plan):
        response = agent.post(f"/plans/{pid}/approve", follow_redirects=False)
        assert response.status_code == 401
    assert agent.get("/", follow_redirects=False).status_code == 303
    assert agent.post(f"/api/v1/plans/{agent_plan}/execute").status_code == 409
    assert adapter.execute_calls == 0
    assert _approvals(audit_path) == []


def test_api_token_is_not_an_approver_login(setup):
    _, _, agent, _, _, _ = setup
    response = agent.post("/login", data={"token": AGENT_TOKEN}, follow_redirects=False)
    assert response.status_code == 401
    assert UI_SESSION_COOKIE not in response.cookies


def test_forged_session_cookie_is_refused(setup):
    _, _, agent, adapter, plan_id, _ = setup
    agent.cookies.set(UI_SESSION_COOKIE, "forged-session-id")
    agent.headers["X-CSRF-Token"] = "forged"
    assert agent.post(f"/plans/{plan_id}/approve").status_code == 401
    assert adapter.execute_calls == 0


def test_session_without_csrf_token_cannot_approve(setup):
    _, human, _, adapter, plan_id, _ = setup
    del human.headers["X-CSRF-Token"]
    assert human.post(f"/plans/{plan_id}/approve").status_code == 403
    wrong = human.post(f"/plans/{plan_id}/approve", data={"csrf_token": "guess"})
    assert wrong.status_code == 403
    assert adapter.execute_calls == 0


def test_csrf_token_of_another_session_is_refused(setup):
    app, human, _, adapter, plan_id, _ = setup
    other = sign_in(TestClient(app), "bob")
    human.headers["X-CSRF-Token"] = other.headers["X-CSRF-Token"]
    assert human.post(f"/plans/{plan_id}/approve").status_code == 403
    assert adapter.execute_calls == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_cross_site_post_is_refused_even_with_session_and_csrf(setup, headers):
    _, human, _, adapter, plan_id, _ = setup
    response = human.post(f"/plans/{plan_id}/approve", headers=headers)
    assert response.status_code == 403
    assert adapter.execute_calls == 0


def test_self_declared_approver_field_is_ignored(setup):
    _, human, _, _, plan_id, audit_path = setup
    human.post(f"/plans/{plan_id}/approve", data={"approver": "the-ceo"})
    (approval,) = _approvals(audit_path)
    assert approval["actor"] == "alice"
    assert approval["payload"]["approver"] == "alice"


def test_logout_kills_the_session_server_side(setup):
    _, human, _, adapter, plan_id, _ = setup
    stolen = human.cookies.get(UI_SESSION_COOKIE)
    csrf = human.headers["X-CSRF-Token"]
    assert human.post("/logout", follow_redirects=False).status_code == 303
    replay = TestClient(human.app)
    replay.cookies.set(UI_SESSION_COOKIE, stolen)
    replay.headers["X-CSRF-Token"] = csrf
    assert replay.post(f"/plans/{plan_id}/approve").status_code == 401
    assert adapter.execute_calls == 0


def test_logout_itself_needs_csrf(setup):
    _, human, _, _, _, _ = setup
    del human.headers["X-CSRF-Token"]
    assert human.post("/logout").status_code == 403
    assert human.get("/", follow_redirects=False).status_code == 200


def test_login_cannot_redirect_off_site(setup):
    _, _, agent, _, _, _ = setup
    response = agent.post(
        "/login",
        data={"token": approver_token("bob"), "next": "//evil.example/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_cross_site_login_is_refused(setup):
    _, _, agent, _, _, _ = setup
    response = agent.post(
        "/login",
        data={"token": approver_token("bob")},
        headers={"Origin": "http://evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert UI_SESSION_COOKIE not in response.cookies


def test_session_cookie_attributes(setup):
    _, _, agent, _, _, _ = setup
    response = agent.post("/login", data={"token": approver_token("bob")}, follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert re.search(r"max-age=\d+", cookie)


def test_ui_cannot_be_framed(setup):
    _, human, agent, _, plan_id, _ = setup
    for response in (human.get(f"/plans/{plan_id}"), agent.get("/login")):
        assert response.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"


def test_anchor_needs_the_api_token_or_an_approver(setup):
    _, human, agent, _, _, _ = setup
    assert agent.post("/audit/anchor").status_code == 401
    wrong = agent.post("/audit/anchor", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    scheduler = agent.post("/audit/anchor", headers={"Authorization": f"Bearer {AGENT_TOKEN}"})
    assert scheduler.status_code == 200
    assert human.post("/audit/anchor").status_code == 200


def test_health_and_audit_head_stay_public(setup):
    _, _, agent, _, _, _ = setup
    assert agent.get("/health").status_code == 200
    assert agent.get("/audit/head").json()["verified"] is True


def test_ui_query_budget_follows_the_approver_not_the_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDACO_QUERY_BUDGET", "1")  # each plan has one query step
    adapter = CountingAdapter()
    planner = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=SequencedLLM(_good_steps(), _good_steps()).transport(),
    )
    app = create_app(
        adapter=adapter, planner=planner, audit_path=str(tmp_path / "a.jsonl"), clearance="internal"
    )
    first = sign_in(TestClient(app), "alice")
    ids = [_ask(first), _ask(first)]
    assert first.post(f"/plans/{ids[0]}/approve").status_code == 200
    # A fresh browser (no cookies at all), same approver: the budget is spent.
    second = sign_in(TestClient(app), "alice")
    assert second.post(f"/plans/{ids[1]}/approve", follow_redirects=False).status_code == 429
    assert adapter.execute_calls == 1
