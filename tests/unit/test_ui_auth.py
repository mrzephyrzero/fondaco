# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Approver authentication building blocks (api/auth.py), fail closed."""

import pytest
from fastapi.testclient import TestClient

import api.auth as auth_mod
from api.auth import (
    BOOTSTRAP_APPROVER,
    MAX_TOKEN_LEN,
    ApproverAuth,
    AuthConfigError,
    approvers_from_env,
    cross_site,
    hash_token,
    main,
    parse_approvers,
    safe_next,
    session_ttl_from_env,
)
from api.main import create_app
from tests.ui_auth import approver_token, sign_in
from tests.unit.test_api import CountingAdapter, _good_steps
from tests.unit.test_planner import SequencedLLM

H1 = hash_token("token-one")
H2 = hash_token("token-two")


# ── configuration parsing ───────────────────────────────────────────────


def test_parse_approvers_accepts_commas_newlines_and_comments():
    text = f"# approvers\nalice:{H1}\n\n bob : {H2.upper()} ,"
    assert parse_approvers(text) == {"alice": H1, "bob": H2}


@pytest.mark.parametrize(
    "text",
    [
        "alice",  # no hash
        f"alice {H1}",  # wrong separator
        "alice:abc",  # short hash
        f":{H1}",  # empty name
        f"al ice:{H1}",  # space in name
        f"<script>:{H1}",  # markup in name
        f"alice:{H1},alice:{H2}",  # duplicate name
        f"alice:{H1},bob:{H1}",  # shared token
    ],
)
def test_parse_approvers_refuses_malformed_entries(text):
    with pytest.raises(AuthConfigError):
        parse_approvers(text)


def test_approvers_from_env_unset_is_none(monkeypatch):
    monkeypatch.delenv("FONDACO_APPROVERS", raising=False)
    monkeypatch.delenv("FONDACO_APPROVERS_FILE", raising=False)
    assert approvers_from_env() is None


def test_approvers_from_file(monkeypatch, tmp_path):
    path = tmp_path / "approvers"
    path.write_text(f"alice:{H1}\n")
    monkeypatch.delenv("FONDACO_APPROVERS", raising=False)
    monkeypatch.setenv("FONDACO_APPROVERS_FILE", str(path))
    assert approvers_from_env() == {"alice": H1}


@pytest.mark.parametrize(
    ("inline", "file_text"),
    [
        (f"alice:{H1}", f"bob:{H2}"),  # both set: ambiguous
        ("", "# only a comment\n"),  # set but empty
        ("", None),  # file missing
    ],
)
def test_approvers_from_env_fails_closed(monkeypatch, tmp_path, inline, file_text):
    path = tmp_path / "approvers"
    if file_text is not None:
        path.write_text(file_text)
    monkeypatch.setenv("FONDACO_APPROVERS", inline)
    monkeypatch.setenv("FONDACO_APPROVERS_FILE", str(path))
    with pytest.raises(AuthConfigError):
        approvers_from_env()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 28800), ("3600", 3600), ("0", 28800), ("-5", 28800), ("forever", 28800), ("10", 28800)],
)
def test_session_ttl_never_unbounded(monkeypatch, raw, expected):
    monkeypatch.setenv("FONDACO_UI_SESSION_TTL_S", raw)
    assert session_ttl_from_env() == expected


# ── tokens and sessions ────────────────────────────────────────────────


def test_authenticate_matches_only_the_exact_token():
    auth = ApproverAuth({"alice": H1, "bob": H2})
    assert auth.authenticate("token-one") == "alice"
    assert auth.authenticate("token-two") == "bob"
    assert auth.authenticate("token-one ") is None
    assert auth.authenticate("") is None
    assert auth.authenticate(H1) is None  # the hash is not the token
    assert auth.authenticate("x" * (MAX_TOKEN_LEN + 1)) is None


def test_session_lifecycle_expiry_and_logout(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(auth_mod.time, "monotonic", lambda: now[0])
    auth = ApproverAuth({"alice": H1}, session_ttl_s=60)
    sid = auth.login("alice")
    current = auth.session(sid)
    assert current is not None and current.approver == "alice"
    assert auth.csrf_ok(current, current.csrf)
    assert not auth.csrf_ok(current, "nope")
    assert not auth.csrf_ok(current, None)
    assert auth.session("forged") is None

    now[0] += 61
    assert auth.session(sid) is None  # expired, and dropped

    sid2 = auth.login("alice")
    auth.logout(sid2)
    assert auth.session(sid2) is None


def test_sessions_are_distinct_and_carry_distinct_csrf():
    auth = ApproverAuth({"alice": H1})
    a, b = auth.login("alice"), auth.login("alice")
    assert a != b
    assert auth.session(a).csrf != auth.session(b).csrf
    assert not auth.csrf_ok(auth.session(a), auth.session(b).csrf)


def test_api_token_may_not_double_as_an_approver_token():
    auth = ApproverAuth({"alice": H1})
    auth.check_separate_from("token-two")  # different: fine
    with pytest.raises(AuthConfigError):
        auth.check_separate_from("token-one")


# ── request helpers ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/plans/abc", "/plans/abc"),
        ("/audit?plan=x", "/audit?plan=x"),
        ("//evil.example/x", "/"),
        ("/\\evil.example", "/"),
        ("https://evil.example/", "/"),
        ("javascript:alert(1)", "/"),
        ("/ok\r\nSet-Cookie: x=y", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_safe_next_stays_on_site(target, expected):
    assert safe_next(target) == expected


@pytest.mark.parametrize(
    ("headers", "cross"),
    [
        ({}, False),
        ({"origin": "http://fondaco.local:8000"}, False),
        ({"origin": "http://evil.example"}, True),
        ({"origin": "http://fondaco.local:8000.evil.example"}, True),
        ({"origin": "null"}, True),
        ({"sec-fetch-site": "same-origin"}, False),
        ({"sec-fetch-site": "none"}, False),
        ({"sec-fetch-site": "cross-site"}, True),
        ({"sec-fetch-site": "same-site"}, True),
        # Sec-Fetch-Site wins: a same-origin post may carry Origin: null.
        ({"sec-fetch-site": "same-origin", "origin": "null"}, False),
        ({"sec-fetch-site": "cross-site", "origin": "http://fondaco.local:8000"}, True),
    ],
)
def test_cross_site_detection(headers, cross):
    assert cross_site(headers, "fondaco.local:8000") is cross


def test_cli_mints_a_token_and_its_matching_entry(capsys):
    assert main(["new-approver", "alice"]) == 0
    out = capsys.readouterr().out.splitlines()
    token = out[0].rsplit(": ", 1)[1]
    entry = out[1].rsplit(": ", 1)[1]
    assert parse_approvers(entry) == {"alice": hash_token(token)}
    assert len(token) >= 40
    assert main(["new-approver", "bad name"]) == 2


# ── app startup ────────────────────────────────────────────────────────


def _app(tmp_path, **kwargs):
    from planner.client import PlannerClient

    planner = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=SequencedLLM(_good_steps()).transport(),
    )
    return create_app(
        adapter=CountingAdapter(),
        planner=planner,
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
        **kwargs,
    )


def test_no_approvers_configured_means_a_bootstrap_token_not_open_access(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("FONDACO_APPROVERS", raising=False)
    monkeypatch.delenv("FONDACO_APPROVERS_FILE", raising=False)
    client = TestClient(_app(tmp_path))
    assert client.get("/", follow_redirects=False).status_code == 303  # still locked

    printed = capsys.readouterr().err
    token = printed.rsplit(": ", 1)[1].strip()
    page = client.post("/login", data={"token": token})
    assert page.status_code == 200
    assert f"signed in as <strong>{BOOTSTRAP_APPROVER}</strong>" in page.text


def test_bootstrap_tokens_differ_per_start(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("FONDACO_APPROVERS", raising=False)
    monkeypatch.delenv("FONDACO_APPROVERS_FILE", raising=False)
    _app(tmp_path)
    _app(tmp_path)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if "one-time token" in ln]
    assert len(lines) == 2 and lines[0] != lines[1]


def test_malformed_approvers_stop_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDACO_APPROVERS", "alice:not-a-hash")
    with pytest.raises(AuthConfigError):
        _app(tmp_path)


def test_api_token_equal_to_an_approver_token_stops_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDACO_API_TOKEN", approver_token("alice"))
    with pytest.raises(AuthConfigError):
        _app(tmp_path)


def test_signed_in_pages_show_the_authenticated_approver(tmp_path):
    client = sign_in(TestClient(_app(tmp_path)), "bob")
    page = client.get("/")
    assert "signed in as <strong>bob</strong>" in page.text
    assert 'name="csrf_token"' in page.text
