# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Audit log: append-only by construction, tampering breaks the hash chain."""

import json

import pytest

from boundary.audit import (
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    AuditError,
    AuditLog,
    _entry_hash,
)


def _seeded_log(path) -> AuditLog:
    log = AuditLog(path)
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q1"})
    log.append(EVENT_POLICY_DECISION, {"allow": True, "reason_code": "allow"})
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q2"})
    return log


def test_clean_chain_verifies(tmp_path):
    log = _seeded_log(tmp_path / "audit.jsonl")
    result = log.verify()
    assert result.ok is True
    assert result.entries == 3


def test_chain_survives_reopen(tmp_path):
    path = tmp_path / "audit.jsonl"
    _seeded_log(path)
    reopened = AuditLog(path)
    reopened.append(EVENT_QUESTION_RECEIVED, {"question": "q3"})
    result = reopened.verify()
    assert result.ok is True
    assert result.entries == 4


def _lines(path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _write_lines(path, lines) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_edited_entry_detected(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    lines = _lines(path)
    entry = json.loads(lines[1])
    entry["payload"]["allow"] = False  # forge the recorded decision
    lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    _write_lines(path, lines)
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 1
    assert result.reason == "hash mismatch"


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
    result = log.verify()
    assert result.ok is False


def test_opening_tampered_log_refuses_append(tmp_path):
    path = tmp_path / "audit.jsonl"
    _seeded_log(path)
    lines = _lines(path)
    del lines[0]
    _write_lines(path, lines)
    with pytest.raises(AuditError):
        AuditLog(path)


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
    assert exposed == {"append", "verify", "entries"}  # append-only + read-only views


# ── The chain itself: what prev_hash catches that nothing else does ────────
#
# verify() checks three things per entry, in order: the seq counter, the
# prev_hash link, and the entry's own hash. The tampering tests above are
# caught by the first or the third — a plain deletion trips the seq counter,
# a plain edit trips the entry's own hash — so none of them ever reaches the
# link check, and a coverage run shows the "chain break" return unexecuted.
#
# The tests below model the next attacker up: one who knows the format and
# repairs everything *local* to what they touched — renumbers seq, recomputes
# the entry's own hash. Only the link to the neighbouring entry is left
# broken, so prev_hash is the one check standing between them and a clean
# verify.
#
# The limit of the property, stated so it is not over-read: an attacker who
# also relinks prev_hash is rewriting the whole chain from the point of the
# edit, and that is undetectable — see tests/adversarial/test_audit_forgery.py.


def _rehashed(entry: dict) -> str:
    """Recompute an entry's own hash exactly as append() does."""
    body = {k: v for k, v in entry.items() if k != "hash"}
    entry["hash"] = _entry_hash(body)
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def test_edit_with_recomputed_hash_is_caught_by_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    entries = [json.loads(line) for line in _lines(path)]

    entries[1]["payload"]["allow"] = False  # forge the recorded decision
    lines = [json.dumps(e, sort_keys=True, separators=(",", ":")) for e in entries]
    lines[1] = _rehashed(entries[1])  # ...and make the entry self-consistent
    _write_lines(path, lines)

    result = log.verify()
    assert result.ok is False
    # The forged entry itself verifies; its successor's link does not.
    assert result.bad_seq == 2
    assert result.reason == "chain break"


def test_deletion_with_renumbered_seq_is_caught_by_the_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _seeded_log(path)
    entries = [json.loads(line) for line in _lines(path)]

    del entries[1]  # erase the policy decision...
    for seq, entry in enumerate(entries):  # ...close the gap in the counter...
        entry["seq"] = seq
    lines = [_rehashed(e) for e in entries]  # ...and re-seal every entry
    _write_lines(path, lines)

    result = log.verify()
    assert result.ok is False
    # seq is contiguous again and every entry's own hash matches its body, so
    # the only thing left out of place is the link across the deletion.
    assert result.bad_seq == 1
    assert result.reason == "chain break"
