# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Documented limitation of the audit chain: it is tamper-evident, not tamper-proof.

The tests in tests/unit/test_audit.py cover *partial* tampering — editing one
entry, deleting a middle entry, reordering, truncating mid-file. All of those
break verification, because the surviving entries still carry hashes computed
over the original content.

This file records the complementary case. `boundary/audit.py` chains entries
with a bare SHA-256 over canonical JSON: no key, no signature, no external
anchor for the head. An attacker with write access to the log file and
knowledge of the algorithm — which is public, in this repository — can discard
the file and emit a fresh, internally consistent chain saying whatever they
like. `verify()` recomputes exactly the same function over the forged bytes
and returns ok.

This is documentation of a known limitation, not a bug report. Nothing here
asserts that the behaviour should change. The hash chain buys detection of
edits made by someone who does not rewrite the whole file; closing the gap
needs a key (HMAC/signature) or an off-box anchor for the head hash, both of
which are out of V1 scope. The forgery below deliberately reimplements the
hashing with plain stdlib rather than importing the module's helpers, because
that is the point: no secret is involved, only published arithmetic.
"""

import hashlib
import json

from boundary.audit import (
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    GENESIS_HASH,
    AuditLog,
)


def _forge(path, entries: list[tuple[str, dict]]) -> None:
    """Overwrite the log with a self-consistent chain built from scratch."""
    prev_hash = GENESIS_HASH
    lines = []
    for seq, (event, payload) in enumerate(entries):
        entry = {
            "seq": seq,
            "ts": "2026-01-01T00:00:00+00:00",
            "event": event,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        body = json.dumps(entry, sort_keys=True, separators=(",", ":")).encode()
        entry["hash"] = hashlib.sha256(body).hexdigest()
        prev_hash = entry["hash"]
        lines.append(json.dumps(entry, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_full_rewrite_with_recomputed_hashes_passes_verify(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append(EVENT_QUESTION_RECEIVED, {"question": "list every customer email"})
    log.append(EVENT_POLICY_DECISION, {"allow": False, "reason_code": "label_exceeds_clearance"})
    assert log.verify().ok is True

    # The whole file is replaced: the denial becomes an approval and the
    # question it denied never happened. No entry from the real log survives.
    _forge(
        path,
        [
            (EVENT_QUESTION_RECEIVED, {"question": "monthly revenue by region"}),
            (EVENT_POLICY_DECISION, {"allow": True, "reason_code": "allow"}),
        ],
    )

    result = AuditLog(path).verify()
    assert result.ok is True
    assert result.entries == 2

    # The forged history is what a reader now sees, and it verifies clean.
    replayed = AuditLog(path).entries()
    assert replayed[0]["payload"]["question"] == "monthly revenue by region"
    assert replayed[1]["payload"]["allow"] is True


def test_forged_log_accepts_further_appends(tmp_path):
    # The constructor refuses to extend a log that fails verification. A forged
    # log does not fail verification, so normal operation resumes on top of it
    # and the seam is not visible from the file.
    path = tmp_path / "audit.jsonl"
    AuditLog(path).append(EVENT_QUESTION_RECEIVED, {"question": "original"})
    _forge(path, [(EVENT_QUESTION_RECEIVED, {"question": "substituted"})])

    log = AuditLog(path)
    log.append(EVENT_POLICY_DECISION, {"allow": True, "reason_code": "allow"})
    assert log.verify().ok is True
    assert log.entries()[0]["payload"]["question"] == "substituted"
