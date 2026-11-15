# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Wholesale audit forgery (threat model 13b) against the HMAC chain.

Under the v0 chain (bare SHA-256, no key) anyone with write access who knew
the algorithm could discard the log and emit a fresh, internally consistent
history, and `verify()` returned ok. This file used to pin that limitation.
It now pins its closure, and states precisely where the closure ends:

  * WITHOUT the key, a rewrite — even one that reproduces the exact format,
    sequence numbers and chaining — fails verification, and the log refuses
    to open, so the boundary does not resume on top of it.
  * WITH the key (a compromised host that holds `FONDACO_AUDIT_HMAC_KEY`), a
    rewrite verifies again: a MAC is only as secret as its key. What still
    catches it is an external anchor exported before the rewrite.
  * Tail truncation and outright deletion are not detectable from the log
    alone, keyed or not; an anchor catches both.

The forger below reimplements the sealing with plain stdlib, as an attacker
reading this repository would.
"""

import hashlib
import hmac
import json

import pytest

from boundary.audit import (
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    GENESIS_MAC,
    AuditError,
    AuditLog,
    FileAnchorSink,
    Keyring,
)

REAL_KEY = b"adversarial-real-audit-key-0123456789"
GUESSED_KEY = b"attacker-guess-of-the-key-0000000000"
RING = Keyring("k1", {"k1": REAL_KEY})


def _forge(path, entries: list[tuple[str, dict]], key: bytes, key_id: str = "k1") -> None:
    """Overwrite the log with a self-consistent chain built from scratch."""
    prev_mac = GENESIS_MAC
    lines = []
    for seq, (event, payload) in enumerate(entries):
        entry = {
            "seq": seq,
            "ts": "2026-01-01T00:00:00+00:00",
            "actor": "boundary",
            "event": event,
            "plan_sha256": None,
            "payload": payload,
            "prev_mac": prev_mac,
            "key_id": key_id,
        }
        body = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        entry["mac"] = hmac.new(key, body, hashlib.sha256).hexdigest()
        prev_mac = entry["mac"]
        lines.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


FORGED_HISTORY = [
    (EVENT_QUESTION_RECEIVED, {"question": "monthly revenue by region"}),
    (EVENT_POLICY_DECISION, {"allow": True, "reason_code": "allow"}),
]


def _real_log(path) -> AuditLog:
    log = AuditLog(path, RING)
    log.append(EVENT_QUESTION_RECEIVED, {"question": "list every customer email"})
    log.append(EVENT_POLICY_DECISION, {"allow": False, "reason_code": "label_exceeds_clearance"})
    return log


def test_full_rewrite_without_the_key_fails_verification(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _real_log(path)
    _forge(path, FORGED_HISTORY, key=GUESSED_KEY)
    result = log.verify()
    assert result.ok is False
    assert result.bad_seq == 0
    assert result.reason == "mac mismatch"


def test_rewrite_with_the_old_unkeyed_algorithm_fails_verification(tmp_path):
    # The exact v0 attack: SHA-256 over the body, no key at all.
    path = tmp_path / "audit.jsonl"
    _real_log(path)
    prev = GENESIS_MAC
    lines = []
    for seq, (event, payload) in enumerate(FORGED_HISTORY):
        entry = {
            "seq": seq, "ts": "t", "actor": "boundary", "event": event,
            "plan_sha256": None, "payload": payload, "prev_mac": prev, "key_id": "k1",
        }  # fmt: skip
        entry["mac"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        prev = entry["mac"]
        lines.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(AuditError, match="mac mismatch"):
        AuditLog(path, RING)


def test_forged_log_refuses_further_appends(tmp_path):
    # v0: a forged log verified, so normal operation resumed on top of it and
    # the seam was invisible. Now the boundary refuses to open it at all.
    path = tmp_path / "audit.jsonl"
    _real_log(path)
    _forge(path, FORGED_HISTORY, key=GUESSED_KEY)
    with pytest.raises(AuditError, match="unverifiable"):
        AuditLog(path, RING)


def test_forged_entry_under_an_invented_key_id_is_refused(tmp_path):
    # Claiming a key id the keyring does not hold does not help either.
    path = tmp_path / "audit.jsonl"
    _real_log(path)
    _forge(path, FORGED_HISTORY, key=GUESSED_KEY, key_id="attacker")
    with pytest.raises(AuditError, match="unknown key id"):
        AuditLog(path, RING)


def test_rewrite_with_a_stolen_key_verifies_but_the_anchor_catches_it(tmp_path):
    # The limit of the property, stated so it is not over-read: a MAC is only
    # as secret as its key. A host compromise that reads the key can reseal a
    # forged history and `verify()` passes. An anchor exported to a place the
    # attacker cannot rewrite is what still exposes it.
    path = tmp_path / "audit.jsonl"
    log = _real_log(path)
    anchors = FileAnchorSink(tmp_path / "offbox-anchors.jsonl")
    log.anchor(anchors)

    _forge(path, FORGED_HISTORY + [(EVENT_QUESTION_RECEIVED, {"q": "pad"})], key=REAL_KEY)
    assert AuditLog(path, RING).verify().ok is True  # the stolen key reseals it

    with pytest.raises(AuditError, match="does not match anchor"):
        AuditLog(path, RING, anchor=anchors.latest())


def test_tail_truncation_needs_an_anchor(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = _real_log(path)
    anchors = FileAnchorSink(tmp_path / "anchors.jsonl")
    log.anchor(anchors)
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:1]) + "\n", encoding="utf-8")
    assert AuditLog(path, RING).verify().ok is True  # undetectable from the file
    with pytest.raises(AuditError):
        AuditLog(path, RING, anchor=anchors.latest())
