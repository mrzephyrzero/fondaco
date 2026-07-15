# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Adversarial: aggregate binary-search exfiltration, and how guards answer it.

Attack: one victim order (a unique id) has an unknown `total_amount`. Each
probe is a legal `count` aggregate — "does this specific order exceed
threshold T?" — whose answer is 0 or 1, narrowing T like a binary search.
Every individual plan validates and passes policy; the *sequence* is the
exploit, and the 0/1 count is exactly a one-row group.

Two guards defeat it, and this test proves both end-to-end through the API:
  1. k-threshold: each probe counts a 1-row set → the group is suppressed,
     so the attacker reads nothing.
  2. query budget: the run of probes hard-stops at the per-session limit,
     and the audit log records the guard decisions.
"""

import json

import httpx

from tests.integration.conftest import requires_db

pytestmark = requires_db

VICTIM_ORDER = 1


def _probe_plan_steps(threshold: int) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "id": "s1",
                    "type": "query",
                    "template": (
                        "SELECT total_amount FROM orders "
                        "WHERE id = %(oid)s AND total_amount > %(t)s"
                    ),
                    "params": {
                        "oid": {"type": "int", "value": VICTIM_ORDER},
                        "t": {"type": "int", "value": threshold},
                    },
                },
                {
                    "id": "s2",
                    "type": "aggregate",
                    "input": "s1",
                    "group_by": [],
                    "ops": [{"op": "count", "column": "*", "as": "n"}],
                },
                {
                    "id": "s3",
                    "type": "present",
                    "input": "s2",
                    "format": "scalar",
                    "title": "probe",
                },
            ]
        }
    )


class AttackerPlanner:
    """Feeds a fresh binary-search probe on each /ask, via the mock transport."""

    def __init__(self):
        self._low, self._high = 0, 5000
        self.request_bodies: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.request_bodies.append(request.content.decode("utf-8"))
            threshold = (self._low + self._high) // 2
            self._high = threshold  # keep narrowing regardless of (suppressed) answer
            return httpx.Response(
                200, json={"choices": [{"message": {"content": _probe_plan_steps(threshold)}}]}
            )

        return httpx.MockTransport(handler)


def _make_client(adapter, tmp_path, budget: int, monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import create_app
    from planner.client import PlannerClient

    monkeypatch.setenv("FONDACO_QUERY_BUDGET", str(budget))
    monkeypatch.delenv("FONDACO_GUARD_K", raising=False)  # default k=5
    attacker = AttackerPlanner()
    app = create_app(
        adapter=adapter,
        planner=PlannerClient(
            base_url="https://llm.invalid/v1", model="mock", transport=attacker.transport()
        ),
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
    )
    return TestClient(app), attacker


def test_binary_search_is_suppressed_then_budget_halts(adapter, tmp_path, monkeypatch):
    budget = 8
    client, _ = _make_client(adapter, tmp_path, budget, monkeypatch)

    executed_probes = 0
    suppressed_probes = 0
    blocked = 0
    for _ in range(budget + 4):  # try more probes than the budget allows
        ask = client.post("/ask", data={"question": "probe the victim order"})
        plan_id = str(ask.url).rsplit("/plans/", 1)[1]
        approve = client.post(
            f"/plans/{plan_id}/approve", data={"approver": "attacker"}, follow_redirects=True
        )
        if approve.status_code == 429:
            blocked += 1
            continue
        executed_probes += 1
        page = client.get(f"/plans/{plan_id}")
        # The count group is a single victim row (or zero) → suppressed: the
        # attacker never sees the number that would drive the search.
        if "suppressed" in page.text:
            suppressed_probes += 1

    # 1. Budget hard stop: no more than `budget` probes ever executed.
    assert executed_probes == budget
    assert blocked >= 1

    # 2. Every executed probe leaked nothing — its result group was suppressed.
    assert suppressed_probes == executed_probes

    # 3. The audit log makes the attack visible.
    entries = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    guard_events = [e["payload"] for e in entries if e["event"] == "guard_decision"]
    k_hits = [g for g in guard_events if g["guard"] == "k_threshold" and g["triggered"]]
    budget_hits = [g for g in guard_events if g["guard"] == "query_budget" and g["triggered"]]
    assert len(k_hits) == budget  # every executed probe suppressed a group
    assert len(budget_hits) >= 1  # the run was halted, and it is on the record

    audit_page = client.get("/audit", params={"event": "guard_decision"})
    assert "query_budget" in audit_page.text and "k_threshold" in audit_page.text


def test_canary_value_never_recoverable_from_results(adapter, tmp_path, monkeypatch):
    """Even reading every executed probe's page, the victim value stays hidden."""
    client, _ = _make_client(adapter, tmp_path, 6, monkeypatch)
    leaked_numbers = []
    for _ in range(6):
        ask = client.post("/ask", data={"question": "probe"})
        plan_id = str(ask.url).rsplit("/plans/", 1)[1]
        client.post(f"/plans/{plan_id}/approve", data={}, follow_redirects=True)
        page = client.get(f"/plans/{plan_id}")
        # A suppressed result renders no data row — assert the result table is empty.
        assert "suppressed" in page.text
        leaked_numbers.append("<td>1</td>" in page.text or "<td>0</td>" in page.text)
    assert not any(leaked_numbers)
