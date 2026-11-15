# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""The API halts when the audit chain breaks, and exposes head + anchoring.

`verify_chain()` runs at the start of every boundary operation (ask, approve,
reject). A broken, truncated or deleted log turns every one of them into an
HTTP 503 — and, crucially, an approval on a halted boundary never reaches the
adapter.
"""

import json

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from boundary.audit import AuditError, FileAnchorSink, plan_sha256
from planner.client import PlannerClient
from tests.ui_auth import sign_in
from tests.unit.test_api import CountingAdapter, _ask, _good_steps
from tests.unit.test_planner import SequencedLLM


def _client(tmp_path, *llm_texts, anchor_sink=None):
    llm = SequencedLLM(*llm_texts)
    adapter = CountingAdapter()
    planner = PlannerClient(
        base_url="https://llm.invalid/v1", model="mock", max_attempts=2, transport=llm.transport()
    )
    app = create_app(
        adapter=adapter,
        planner=planner,
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
        anchor_sink=anchor_sink,
    )
    return sign_in(TestClient(app), "dana"), adapter, tmp_path / "audit.jsonl"


def _entries(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_entries_bind_actor_and_plan_digest(tmp_path):
    client, adapter, path = _client(tmp_path, _good_steps())
    plan_id = _ask(client)
    client.post(f"/plans/{plan_id}/approve", data={"approver": "dana"})
    entries = _entries(path)
    approval = next(e for e in entries if e["event"] == "approval")
    assert approval["actor"] == "dana"
    digests = {e["plan_sha256"] for e in entries if e["event"] != "question_received"}
    assert len(digests) == 1  # every plan event commits to the same plan
    assert None not in digests
    question = entries[0]
    assert question["event"] == "question_received"
    assert question["plan_sha256"] is None


def test_tampered_log_halts_approval_before_execution(tmp_path):
    client, adapter, path = _client(tmp_path, _good_steps())
    plan_id = _ask(client)
    path.write_text(path.read_text().replace("orders per region", "orders per REGION"))
    response = client.post(f"/plans/{plan_id}/approve", data={"approver": "dana"})
    assert response.status_code == 503
    assert "boundary halted" in response.text
    assert adapter.execute_calls == 0


def test_deleted_log_halts_every_operation(tmp_path):
    client, adapter, path = _client(tmp_path, _good_steps(), _good_steps())
    plan_id = _ask(client)
    path.unlink()
    assert client.post("/ask", data={"question": "again"}).status_code == 503
    assert client.post(f"/plans/{plan_id}/approve", data={"approver": "x"}).status_code == 503
    assert client.post(f"/plans/{plan_id}/reject", data={"approver": "x"}).status_code == 503
    assert adapter.execute_calls == 0
    assert client.get("/audit/head").status_code == 503


def test_audit_head_reports_the_verified_head(tmp_path):
    client, adapter, path = _client(tmp_path, _good_steps())
    _ask(client)
    body = client.get("/audit/head").json()
    last = _entries(path)[-1]
    assert body["verified"] is True
    assert body["head"] == {
        "seq": last["seq"], "mac": last["mac"], "key_id": last["key_id"], "ts": last["ts"],
    }  # fmt: skip


def test_anchor_endpoint_exports_to_the_sink(tmp_path):
    sink = FileAnchorSink(tmp_path / "anchors.jsonl")
    client, adapter, path = _client(tmp_path, _good_steps(), anchor_sink=sink)
    _ask(client)
    response = client.post("/audit/anchor")
    assert response.status_code == 200
    anchored = response.json()["anchored"]
    assert sink.latest().to_dict() == anchored
    assert _entries(path)[-1]["event"] == "anchor_exported"


def test_anchor_endpoint_without_sink_is_a_conflict(tmp_path):
    client, adapter, path = _client(tmp_path, _good_steps())
    assert client.post("/audit/anchor").status_code == 409


def test_anchor_endpoint_on_broken_chain_refuses(tmp_path):
    sink = FileAnchorSink(tmp_path / "anchors.jsonl")
    client, adapter, path = _client(tmp_path, _good_steps(), anchor_sink=sink)
    _ask(client)
    path.write_text(path.read_text().replace("orders per region", "x"))
    assert client.post("/audit/anchor").status_code == 503
    assert not (tmp_path / "anchors.jsonl").exists()


def test_app_refuses_to_start_without_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FONDACO_AUDIT_HMAC_KEY", raising=False)
    with pytest.raises(AuditError):
        create_app(adapter=CountingAdapter(), audit_path=str(tmp_path / "audit.jsonl"))


def test_plan_sha256_is_canonical():
    assert plan_sha256({"a": 1, "b": [1, 2]}) == plan_sha256({"b": [1, 2], "a": 1})
