# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Append-only audit log: JSONL with a SHA-256 hash chain.

Every boundary crossing is logged: question, generated plan, validation
result, policy decision, approval identity, execution digest. Entries
are chained (each carries the previous entry's hash), so any edit,
deletion, reordering, or mid-file truncation breaks verification.

Append-only by construction: this module exposes no update or delete
operation, opens the file in append mode only, and refuses to extend a
log that fails verification. Stdlib only.

Known limitation (for the Phase 7 threat model): removing entries from
the *tail* of the file is not detectable from the file alone — a hash
chain anchors the past, not the future. Detection requires anchoring the
latest hash externally (backup, monitoring, or a countersigned head).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

GENESIS_HASH = "0" * 64

EVENT_QUESTION_RECEIVED = "question_received"
EVENT_PLAN_GENERATED = "plan_generated"
EVENT_VALIDATION_RESULT = "validation_result"
EVENT_POLICY_DECISION = "policy_decision"
EVENT_APPROVAL = "approval"
EVENT_EXECUTION_DIGEST = "execution_digest"


class AuditError(Exception):
    """Raised when the log cannot be safely appended to (fail closed)."""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    bad_seq: int | None = None
    reason: str | None = None


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _entry_hash(entry_without_hash: dict) -> str:
    return hashlib.sha256(_canonical(entry_without_hash)).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        result = self.verify()
        if not result.ok:
            raise AuditError(f"refusing to append to unverifiable log: {result.reason}")
        self._next_seq = result.entries
        self._prev_hash = self._last_hash()

    def _last_hash(self) -> str:
        last = GENESIS_HASH
        if self._path.exists():
            with self._path.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        last = json.loads(line)["hash"]
        return last

    def append(self, event: str, payload: dict) -> dict:
        """Append one entry. Raises AuditError instead of logging partially."""
        entry = {
            "seq": self._next_seq,
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "payload": payload,
            "prev_hash": self._prev_hash,
        }
        try:
            entry["hash"] = _entry_hash(entry)
            line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AuditError(f"unserializable audit payload: {type(exc).__name__}") from exc
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._next_seq += 1
        self._prev_hash = entry["hash"]
        return entry

    def verify(self) -> VerifyResult:
        """Recompute the whole chain; report the first broken entry."""
        if not self._path.exists():
            return VerifyResult(ok=True, entries=0)
        prev_hash = GENESIS_HASH
        count = 0
        try:
            with self._path.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    claimed_hash = entry.get("hash")
                    body = {k: v for k, v in entry.items() if k != "hash"}
                    if entry.get("seq") != count:
                        return VerifyResult(False, count, bad_seq=count, reason="sequence break")
                    if entry.get("prev_hash") != prev_hash:
                        return VerifyResult(False, count, bad_seq=count, reason="chain break")
                    if _entry_hash(body) != claimed_hash:
                        return VerifyResult(False, count, bad_seq=count, reason="hash mismatch")
                    prev_hash = claimed_hash
                    count += 1
        except (OSError, ValueError) as exc:
            reason = f"unreadable: {type(exc).__name__}"
            return VerifyResult(False, count, bad_seq=count, reason=reason)
        return VerifyResult(ok=True, entries=count)
