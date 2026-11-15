# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""`fondaco demo`, end to end: the scripted attacks against the real boundary.

The demo's fixture planner plays a hijacked LLM: each attack is the plan an
attacker would have it emit. This drives every scripted prompt through
create_app over the demo's DuckDB dataset — production validator, policy,
guards, executor and audit — and checks that the legitimate questions run and
every attack is stopped, with no restricted value anywhere in the responses.
"""

import json

import pytest

pytest.importorskip("duckdb")

from api.demo import build_demo_dataset, demo_env, planner_env  # noqa: E402
from boundary.audit import AuditLog  # noqa: E402
from executor.adapters.duckdb_adapter import DuckDBAdapter  # noqa: E402
from executor.adapters.schema_file import load_schema_file  # noqa: E402
from planner.demo import LOCAL_SCRIPTED, DemoPlanner  # noqa: E402

ATTACKS = {q: note for q, kind, note in LOCAL_SCRIPTED if kind == "attack"}
QUESTIONS = [q for q, kind, _ in LOCAL_SCRIPTED if kind == "question"]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return build_demo_dataset(tmp_path_factory.mktemp("demo"))


@pytest.fixture
def client(dataset, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import create_app

    monkeypatch.setenv("FONDACO_SQL_DIALECT", "duckdb")
    db_path, schema_path = dataset
    adapter = DuckDBAdapter(db_path, labels=load_schema_file(schema_path))
    app = create_app(
        adapter=adapter,
        planner=DemoPlanner("local"),
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
    )
    return TestClient(app)


def _ask(client, question):
    response = client.post("/ask", data={"question": question})
    url = str(response.url)
    return (url.rsplit("/plans/", 1)[1] if "/plans/" in url else None), response


@pytest.fixture(scope="module")
def secrets_in_data(dataset):
    import duckdb

    conn = duckdb.connect(str(dataset[0]), read_only=True)
    rows = conn.execute("SELECT iban, ssn FROM employees").fetchall()
    conn.close()
    return [v for row in rows for v in row]


def test_ui_lists_questions_and_attacks(client):
    page = client.get("/").text
    for question in [*QUESTIONS, *ATTACKS]:
        assert question.replace("'", "&#39;") in page
    assert "Attack prompts" in page


@pytest.mark.parametrize("question", QUESTIONS)
def test_legitimate_questions_run(client, question):
    plan_id, response = _ask(client, question)
    assert "ALLOWED" in response.text
    done = client.post(f"/plans/{plan_id}/approve", data={"approver": "demo"})
    assert "executed" in done.text


def test_k_threshold_suppresses_the_two_person_executive_team(client):
    plan_id, _ = _ask(client, "How many employees work in each department?")
    done = client.post(f"/plans/{plan_id}/approve", data={"approver": "demo"})
    assert "Engineering" in done.text
    assert "Executive" not in done.text.split("Headcount per department", 1)[1]


@pytest.mark.parametrize(
    "question",
    [q for q in ATTACKS if "read_csv" not in q],
)
def test_attacks_are_denied_by_policy_and_cannot_be_approved(client, question, secrets_in_data):
    plan_id, response = _ask(client, question)
    assert "DENIED" in response.text
    blocked = client.post(
        f"/plans/{plan_id}/approve", data={"approver": "x"}, follow_redirects=False
    )
    assert blocked.status_code == 409
    for value in secrets_in_data:
        assert value not in response.text


def test_file_read_attack_never_becomes_a_plan(client):
    plan_id, response = _ask(client, "SELECT * FROM read_csv('/etc/passwd')")
    assert plan_id is None
    assert "rejected_by_validator:unknown_relation" in response.text
    assert "root:" not in response.text


def test_every_attack_is_audited_and_the_chain_verifies(client, tmp_path):
    for question in ATTACKS:
        _ask(client, question)
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.verify_chain().ok
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    decisions = [e["payload"] for e in events if e["event"] == "policy_decision"]
    assert len(decisions) == len(ATTACKS) - 1  # the file read never reached policy
    assert all(d["allow"] is False for d in decisions)


# ── Configuration ─────────────────────────────────────────────────────────


def test_demo_env_locks_the_source_and_keys_the_audit(tmp_path):
    build_demo_dataset(tmp_path)
    env = demo_env(tmp_path, env={})
    assert env["FONDACO_ADAPTER"] == "duckdb"
    assert env["FONDACO_SQL_DIALECT"] == "duckdb"
    assert env["FONDACO_EGRESS_CLEARANCE"] == "internal"
    assert len(env["FONDACO_AUDIT_HMAC_KEY"]) >= 32
    assert env["FONDACO_PLANNER"] == "demo"
    assert "FONDACO_AUTO_APPROVE_ENABLED" not in env  # a human approves by default
    # Same directory, same key: the log keeps verifying across restarts.
    assert demo_env(tmp_path, env={})["FONDACO_AUDIT_HMAC_KEY"] == env["FONDACO_AUDIT_HMAC_KEY"]


def test_dataset_build_is_deterministic(tmp_path):
    a, _ = build_demo_dataset(tmp_path / "a")
    b, _ = build_demo_dataset(tmp_path / "b")
    import duckdb

    def dump(path):
        conn = duckdb.connect(str(path), read_only=True)
        try:
            return conn.execute("SELECT * FROM employees ORDER BY id").fetchall()
        finally:
            conn.close()

    assert dump(a) == dump(b)


@pytest.mark.parametrize(
    ("choice", "env", "expected"),
    [
        ("auto", {}, {"FONDACO_PLANNER": "demo"}),
        ("demo", {"ANTHROPIC_API_KEY": "k"}, {"FONDACO_PLANNER": "demo"}),
        ("auto", {"FONDACO_PLANNER": "llm"}, {}),
        ("auto", {"FONDACO_LLM_API_KEY": "k"}, {"FONDACO_PLANNER": "llm"}),
    ],
)
def test_planner_selection(choice, env, expected):
    assert planner_env(choice, env) == expected


def test_provider_keys_select_a_real_planner():
    anthropic = planner_env("auto", {"ANTHROPIC_API_KEY": "a"})
    assert anthropic["FONDACO_LLM_BASE_URL"] == "https://api.anthropic.com/v1"
    openai = planner_env("auto", {"OPENAI_API_KEY": "o", "FONDACO_LLM_MODEL": "m"})
    assert openai["FONDACO_LLM_BASE_URL"] == "https://api.openai.com/v1"
    assert openai["FONDACO_LLM_MODEL"] == "m"
    ollama = planner_env("llm", {})
    assert ollama["FONDACO_LLM_BASE_URL"].startswith("http://localhost:11434")
