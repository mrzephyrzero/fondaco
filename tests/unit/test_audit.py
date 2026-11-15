# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Audit log: append-only by construction, HMAC-sealed, fail closed.

Every property the unkeyed v0 chain had is kept and re-pinned here (edit,
delete, reorder, garbage, refusal to extend a broken log, no partial writes,
no mutation API). The keyed chain adds: a missing or weak key refuses to run,
key rotation, actor and plan digest sealed into every entry, a halt that
sticks once verification fails, and detection of a log that shrinks or
disappears while the boundary is running.
"""

import hashlib
import hmac
import json

import httpx
import pytest

from boundary.audit import (
    EVENT_ANCHOR_EXPORTED,
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    GENESIS_MAC,
    Anchor,
    AuditError,
    AuditLog,
    FileAnchorSink,
    FileBackend,
    Keyring,
    WebhookAnchorSink,
    anchor_sink_from_env,
    audit_log_from_env,
    plan_sha256,
)

KEY = b"unit-test-audit-key-0123456789abcdef"
KEY2 = b"unit-test-rotated-key-fedcba9876543210"


def _ring(active: str = "k1", **keys: bytes) -> Keyring:
    return Keyring(active, keys or {"k1": KEY})


def _seeded_log(path, keyring: Keyring | None = None) -> AuditLog:
    log = AuditLog(path, keyring or _ring())
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q1"})
    log.append(EVENT_POLICY_DECISION, {"allow": True, "reason_code": "allow"})
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q2"})
    return log


def _lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _write_lines(path, lines) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dump(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def _reseal(entry: dict, key: bytes = KEY) -> dict:
    """Recompute an entry's MAC exactly as append() does — needs the key."""
    body = {k: v for k, v in entry.items() if k != "mac"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    entry["mac"] = hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return entry


# ── Clean operation ──────────────────────────────────────────────────────


def test_clean_chain_verifies(tmp_path):
    log = _seeded_log(tmp_path / "audit.jsonl")
    result = log.verify()
    assert result.ok is True
    assert result.entries == 3


def test_chain_survives_reopen(tmp_path):
    path = tmp_path / "audit.jsonl"
    _seeded_log(path)
    reopened = AuditLog(path, _ring())
    reopened.append(EVENT_QUESTION_RECEIVED, {"question": "q3"})
    result = reopened.verify()
    assert result.ok is True
    assert result.entries == 4


def test_entry_shape_seals_actor_and_plan_digest(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", _ring())
    plan = {"plan_id": "p", "steps": [{"id": "s1"}]}
    entry = log.append(EVENT_POLICY_DECISION, {"allow": True}, actor="alice", plan=plan)
    assert set(entry) == {
        "seq", "ts", "actor", "event", "plan_sha256", "payload", "prev_mac", "key_id", "mac",
    }  # fmt: skip
    assert entry["actor"] == "alice"
    assert entry["plan_sha256"] == plan_sha256(plan)
    assert entry["prev_mac"] == GENESIS_MAC
    assert entry["key_id"] == "k1"
    assert log.append(EVENT_QUESTION_RECEIVED, {})["prev_mac"] == entry["mac"]


def test_default_keyring_comes_from_the_environment(tmp_path):
    # tests/conftest.py sets FONDACO_AUDIT_HMAC_KEY for the whole suite.
    log = AuditLog(tmp_path / "audit.jsonl")
    assert log.key_id == "k1"
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q"})
    assert log.verify().ok is True


# ── Tampering (the v0 properties, re-pinned) ──────────────────────────────


def test_edited_entry_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    lines = _lines(path)
    entry = json.loads(lines[1])
    entry["payload"]["allow"] = False  # forge the recorded decision
    lines[1] = _dump(entry)
    _write_lines(path, lines)
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 1
    assert result.reason == "mac mismatch"


@pytest.mark.parametrize("field", ["actor", "plan_sha256", "ts", "event", "key_id"])
def test_every_sealed_field_is_covered_by_the_mac(tmp_path, field):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    lines = _lines(path)
    entry = json.loads(lines[1])
    entry[field] = "k1x" if field == "key_id" else "forged"
    lines[1] = _dump(entry)
    _write_lines(path, lines)
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 1


def test_deleted_middle_entry_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    lines = _lines(path)
    del lines[1]
    _write_lines(path, lines)
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 1


def test_reordered_entries_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    lines = _lines(path)
    lines[0], lines[1] = lines[1], lines[0]
    _write_lines(path, lines)
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 0


def test_garbage_line_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    lines = _lines(path)
    lines[2] = "not json at all"
    _write_lines(path, lines)
    assert log.verify().ok is False


def test_opening_tampered_log_refuses_append(tmp_path):
    path = tmp_path / "audit.jsonl"
    _seeded_log(path)
    lines = _lines(path)
    del lines[0]
    _write_lines(path, lines)
    with pytest.raises(AuditError):
        AuditLog(path, _ring())


def test_unserializable_payload_fails_closed(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    before = _lines(path)
    with pytest.raises(AuditError):
        log.append(EVENT_QUESTION_RECEIVED, {"bad": object()})
    assert _lines(path) == before  # nothing partial was written
    assert log.verify().ok is True


def test_no_mutation_api():
    exposed = {name for name in dir(AuditLog) if not name.startswith("_")}
    # append-only + read-only views + anchoring; nothing updates or deletes.
    assert exposed == {
        "append", "verify", "verify_chain", "entries", "head", "anchor",
        "verify_against_anchor", "halted", "key_id",
    }  # fmt: skip


def test_legacy_unkeyed_log_is_refused(tmp_path):
    # A v0 SHA-256 entry ({..., "prev_hash", "hash"}) can prove nothing under
    # the keyed scheme. The log refuses to extend it; archive and start anew.
    path = tmp_path / "audit.jsonl"
    legacy = {
        "seq": 0,
        "ts": "2026-01-01T00:00:00+00:00",
        "event": EVENT_QUESTION_RECEIVED,
        "payload": {"question": "q"},
        "prev_hash": "0" * 64,
    }
    legacy["hash"] = hashlib.sha256(_dump(legacy).encode()).hexdigest()
    _write_lines(path, [_dump(legacy)])
    with pytest.raises(AuditError, match="legacy or malformed"):
        AuditLog(path, _ring())


# ── The chain link: what prev_mac catches for a key-holding insider ───────
#
# Without the key nobody can produce a valid MAC (see
# tests/adversarial/test_audit_forgery.py). These two tests model the
# insider who *has* the key and repairs everything local to an edit: the
# entry's own MAC verifies, so only the link to its neighbour is left
# broken, and prev_mac is the one check that catches it.


def test_edit_with_recomputed_mac_is_caught_by_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    entries = [json.loads(line) for line in _lines(path)]
    entries[1]["payload"]["allow"] = False
    _reseal(entries[1])
    _write_lines(path, [_dump(e) for e in entries])
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 2
    assert result.reason == "chain break"


def test_deletion_with_renumbered_seq_is_caught_by_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    entries = [json.loads(line) for line in _lines(path)]
    del entries[1]
    for seq, entry in enumerate(entries):
        entry["seq"] = seq
        _reseal(entry)
    _write_lines(path, [_dump(e) for e in entries])
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 1
    assert result.reason == "chain break"


# ── Keys ──────────────────────────────────────────────────────────────────


def test_missing_key_refuses_to_run(tmp_path, monkeypatch):
    monkeypatch.delenv("FONDACO_AUDIT_HMAC_KEY", raising=False)
    with pytest.raises(AuditError, match="FONDACO_AUDIT_HMAC_KEY"):
        AuditLog(tmp_path / "audit.jsonl")


def test_short_key_refuses_to_run():
    with pytest.raises(AuditError, match="shorter than 32 bytes"):
        Keyring("k1", {"k1": b"too-short"})


@pytest.mark.parametrize("key_id", ["", "has space", "x" * 33, "k1/../"])
def test_malformed_key_id_refused(key_id):
    with pytest.raises(AuditError):
        Keyring(key_id, {key_id: KEY})


def test_keyring_repr_never_shows_key_material():
    assert KEY.decode() not in repr(_ring())


def test_wrong_key_fails_verification(tmp_path):
    path = tmp_path / "audit.jsonl"
    _seeded_log(path)
    impostor = Keyring("k1", {"k1": KEY2})
    with pytest.raises(AuditError, match="mac mismatch"):
        AuditLog(path, impostor)


def test_key_rotation_keeps_old_entries_verifiable(tmp_path):
    path = tmp_path / "audit.jsonl"
    _seeded_log(path)  # sealed with k1
    rotated = Keyring("k2", {"k2": KEY2, "k1": KEY})
    log = AuditLog(path, rotated)
    entry = log.append(EVENT_QUESTION_RECEIVED, {"question": "after rotation"})
    assert entry["key_id"] == "k2"
    assert log.verify().ok is True
    # Dropping the retired key makes the old entries unverifiable: fail closed.
    with pytest.raises(AuditError, match="unknown key id"):
        AuditLog(path, Keyring("k2", {"k2": KEY2}))


def test_keyring_from_env_parses_retired_keys():
    env = {
        "FONDACO_AUDIT_HMAC_KEY": KEY2.decode(),
        "FONDACO_AUDIT_HMAC_KEY_ID": "k2",
        "FONDACO_AUDIT_HMAC_RETIRED_KEYS": f"k1:{KEY.decode()}",
    }
    ring = Keyring.from_env(env)
    assert ring.active_id == "k2"
    assert ring.key("k1") == KEY


@pytest.mark.parametrize(
    "retired", ["nocolon", "k1:", "k1:" + KEY.decode() + ",k1:" + KEY.decode()]
)
def test_keyring_from_env_rejects_malformed_retired_keys(retired):
    env = {
        "FONDACO_AUDIT_HMAC_KEY": KEY2.decode(),
        "FONDACO_AUDIT_HMAC_KEY_ID": "k2",
        "FONDACO_AUDIT_HMAC_RETIRED_KEYS": retired,
    }
    with pytest.raises(AuditError):
        Keyring.from_env(env)


# ── verify_chain() halts the boundary, and the halt sticks ───────────────


def test_halt_is_sticky_even_if_the_file_is_restored(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('"q1"', '"qX"'), encoding="utf-8")
    assert log.verify_chain().ok is False
    assert log.halted is not None
    path.write_text(original, encoding="utf-8")  # put it back
    with pytest.raises(AuditError, match="halted"):
        log.append(EVENT_QUESTION_RECEIVED, {"question": "after tamper"})


def test_tail_truncation_while_running_halts(tmp_path):
    # Truncating the tail is invisible to the chain alone, but not to a
    # running writer: it knows how long the log was.
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    _write_lines(path, _lines(path)[:2])
    result = log.verify_chain()
    assert result.ok is False
    assert result.reason == "log truncated or missing"
    with pytest.raises(AuditError):
        log.append(EVENT_QUESTION_RECEIVED, {"question": "q"})


def test_deleted_log_while_running_halts(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    path.unlink()
    assert log.verify_chain().ok is False
    with pytest.raises(AuditError):
        log.append(EVENT_QUESTION_RECEIVED, {"question": "q"})
    assert not path.exists()  # nothing was silently re-created


def test_append_after_silent_truncation_is_refused(tmp_path):
    # Even without an explicit verify_chain() call, append checks its head.
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    _write_lines(path, _lines(path)[:1])
    with pytest.raises(AuditError, match="changed underneath"):
        log.append(EVENT_QUESTION_RECEIVED, {"question": "q"})
    assert len(_lines(path)) == 1


def test_second_writer_on_same_backend_is_adopted_after_verification(tmp_path):
    path = tmp_path / "audit.jsonl"
    first = _seeded_log(path)
    second = AuditLog(path, _ring())
    second.append(EVENT_QUESTION_RECEIVED, {"question": "from second"})
    entry = first.append(EVENT_QUESTION_RECEIVED, {"question": "from first"})
    assert entry["seq"] == 4
    assert first.verify().ok is True


# ── Anchoring ─────────────────────────────────────────────────────────────


def test_anchor_exports_head_and_records_the_export(tmp_path):
    log = _seeded_log(tmp_path / "audit.jsonl")
    sink = FileAnchorSink(tmp_path / "anchors.jsonl")
    head = log.anchor(sink)
    assert head.seq == 2
    assert sink.latest() == head
    last = log.entries()[-1]
    assert last["event"] == EVENT_ANCHOR_EXPORTED
    assert last["payload"] == {"anchored_seq": 2, "anchored_mac": head.mac}
    assert log.verify_against_anchor(head).ok is True


def test_anchor_of_empty_log_is_none(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", _ring())
    assert log.anchor(FileAnchorSink(tmp_path / "a.jsonl")) is None
    assert log.head() is None


def test_anchor_refuses_a_broken_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    path.write_text(path.read_text().replace('"q2"', '"qq"'), encoding="utf-8")
    with pytest.raises(AuditError, match="broken chain"):
        log.anchor(FileAnchorSink(tmp_path / "a.jsonl"))
    assert not (tmp_path / "a.jsonl").exists()


def test_anchor_detects_tail_truncation_at_startup(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    sink = FileAnchorSink(tmp_path / "anchors.jsonl")
    log.anchor(sink)
    _write_lines(path, _lines(path)[:2])  # still a valid chain, just shorter
    assert AuditLog(path, _ring()).verify().ok is True  # chain alone can't tell
    with pytest.raises(AuditError, match="does not match anchor"):
        AuditLog(path, _ring(), anchor=sink.latest())


def test_anchor_detects_a_deleted_log_at_startup(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    sink = FileAnchorSink(tmp_path / "anchors.jsonl")
    log.anchor(sink)
    path.unlink()
    with pytest.raises(AuditError):
        AuditLog(path, _ring(), anchor=sink.latest())


def test_anchor_round_trip_and_validation():
    anchor = Anchor(seq=3, mac="a" * 64, key_id="k1", ts="t")
    assert Anchor.from_dict(anchor.to_dict()) == anchor
    for bad in [None, {}, {"seq": -1, "mac": "a" * 64, "key_id": "k1"},
                {"seq": 1, "mac": "zz", "key_id": "k1"},
                {"seq": True, "mac": "a" * 64, "key_id": "k1"},
                {"seq": 1, "mac": "a" * 64, "key_id": "bad id"}]:  # fmt: skip
        with pytest.raises(AuditError):
            Anchor.from_dict(bad)


def test_corrupt_anchor_file_fails_closed(tmp_path):
    path = tmp_path / "anchors.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(AuditError):
        FileAnchorSink(path).latest()


def test_webhook_sink_posts_the_anchor():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(204)

    sink = WebhookAnchorSink(
        "https://anchor.example/head", method="PUT", transport=httpx.MockTransport(handler)
    )
    anchor = Anchor(seq=1, mac="b" * 64, key_id="k1", ts="t")
    sink.export(anchor)
    assert seen == {"method": "PUT", "body": anchor.to_dict()}


def test_webhook_failure_is_an_audit_error_without_the_url():
    presigned = "https://bucket.example/head?X-Amz-Signature=SECRET"  # noqa: S105
    sink = WebhookAnchorSink(
        presigned, transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    with pytest.raises(AuditError) as info:
        sink.export(Anchor(seq=1, mac="b" * 64, key_id="k1", ts="t"))
    assert "SECRET" not in str(info.value)


@pytest.mark.parametrize(
    ("url", "method"), [("http://plain.example", "POST"), ("https://x", "GET")]
)
def test_webhook_sink_requires_https_and_post_or_put(url, method):
    with pytest.raises(AuditError):
        WebhookAnchorSink(url, method=method)


# ── Configuration from the environment ───────────────────────────────────


def test_env_file_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("FONDACO_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("FONDACO_AUDIT_BACKEND", raising=False)
    log = audit_log_from_env()
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q"})
    assert FileBackend(tmp_path / "audit.jsonl").read_all()[0]["event"] == EVENT_QUESTION_RECEIVED


@pytest.mark.parametrize(("backend", "dsn"), [("postgres", ""), ("mongodb", "x"), ("", "")])
def test_env_bad_backend_fails_closed(monkeypatch, backend, dsn):
    monkeypatch.setenv("FONDACO_AUDIT_BACKEND", backend or "  ")
    monkeypatch.setenv("FONDACO_AUDIT_DSN", dsn)
    with pytest.raises(AuditError):
        audit_log_from_env()


def test_env_anchor_file_is_checked_at_startup(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    anchors = tmp_path / "anchors.jsonl"
    log = _seeded_log(path, Keyring.from_env())
    log.anchor(FileAnchorSink(anchors))
    _write_lines(path, _lines(path)[:1])
    monkeypatch.setenv("FONDACO_AUDIT_LOG", str(path))
    monkeypatch.setenv("FONDACO_AUDIT_ANCHOR_FILE", str(anchors))
    monkeypatch.delenv("FONDACO_AUDIT_BACKEND", raising=False)
    with pytest.raises(AuditError):
        audit_log_from_env()


def test_anchor_sink_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FONDACO_AUDIT_ANCHOR_WEBHOOK", raising=False)
    monkeypatch.delenv("FONDACO_AUDIT_ANCHOR_FILE", raising=False)
    assert anchor_sink_from_env() is None
    monkeypatch.setenv("FONDACO_AUDIT_ANCHOR_FILE", str(tmp_path / "a.jsonl"))
    assert isinstance(anchor_sink_from_env(), FileAnchorSink)
    monkeypatch.setenv("FONDACO_AUDIT_ANCHOR_WEBHOOK", "https://anchor.example/x")
    assert isinstance(anchor_sink_from_env(), WebhookAnchorSink)
