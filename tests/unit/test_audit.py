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
