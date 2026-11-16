# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Append-only audit log: an HMAC-SHA256 chain with external anchoring.

Every boundary crossing is logged: question, generated plan, validation
result, policy decision, approval identity, execution digest. Row data never
enters the log.

Each entry is sealed with

    mac_n = HMAC-SHA256(key[key_id], canonical_json({seq, ts, actor, event,
                        plan_sha256, payload, prev_mac, key_id}))

so it commits to its own content *and* to the previous entry's MAC. The key
lives outside the log (`FONDACO_AUDIT_HMAC_KEY`), which is what the v0
unkeyed SHA-256 chain lacked: rewriting the whole log with recomputed seals
(threat model 13b) now requires the key. `key_id` is bound into every MAC and
names the key that sealed the entry, so keys can be rotated: the active key
seals new entries, retired keys stay available to verify old ones.

Two storage backends: a JSONL file (development, tests, single host) and a
PostgreSQL table whose append-only property is enforced by the database
itself (see demo/dataset/init/04_audit_log.sql). Neither exposes an update
or delete operation.

What a key cannot buy — deleting entries from the *tail*, or deleting the
whole log — is covered by anchoring: `anchor()` exports the current head
`(seq, mac, key_id)` to a place the log's writer cannot rewrite (a file on
another volume, a webhook, an S3 presigned URL), and `verify_against_anchor`
proves a later log still contains that exact entry.

Fail closed: the log refuses to open, and refuses every further append, as
soon as verification fails. The boundary calls `verify_chain()` before each
operation and halts (HTTP 503) on a broken or missing chain.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

GENESIS_MAC = "0" * 64
MIN_KEY_BYTES = 32
DEFAULT_KEY_ID = "k1"
DEFAULT_ACTOR = "boundary"

HMAC_KEY_ENV = "FONDACO_AUDIT_HMAC_KEY"
HMAC_KEY_ID_ENV = "FONDACO_AUDIT_HMAC_KEY_ID"
HMAC_RETIRED_KEYS_ENV = "FONDACO_AUDIT_HMAC_RETIRED_KEYS"

EVENT_QUESTION_RECEIVED = "question_received"
EVENT_PLAN_GENERATED = "plan_generated"
EVENT_VALIDATION_RESULT = "validation_result"
EVENT_POLICY_DECISION = "policy_decision"
EVENT_APPROVAL = "approval"
EVENT_GUARD_DECISION = "guard_decision"
EVENT_EXECUTION_DIGEST = "execution_digest"
EVENT_ANCHOR_EXPORTED = "anchor_exported"

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
_MAC_RE = re.compile(r"^[0-9a-f]{64}$")
_ENTRY_FIELDS = frozenset(
    {"seq", "ts", "actor", "event", "plan_sha256", "payload", "prev_mac", "key_id", "mac"}
)


class AuditError(Exception):
    """Raised when the log cannot be safely appended to (fail closed)."""


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    entries: int
    bad_seq: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Anchor:
    """The externally recorded head of the chain at one point in time."""

    seq: int
    mac: str
    key_id: str
    ts: str

    def to_dict(self) -> dict:
        return {"seq": self.seq, "mac": self.mac, "key_id": self.key_id, "ts": self.ts}

    @classmethod
    def from_dict(cls, raw: object) -> Anchor:
        if not isinstance(raw, dict):
            raise AuditError("anchor is not an object")
        seq, mac, key_id, ts = raw.get("seq"), raw.get("mac"), raw.get("key_id"), raw.get("ts")
        if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0):
            raise AuditError("anchor seq invalid")
        if not (isinstance(mac, str) and _MAC_RE.match(mac)):
            raise AuditError("anchor mac invalid")
        if not (isinstance(key_id, str) and _KEY_ID_RE.match(key_id)):
            raise AuditError("anchor key_id invalid")
        return cls(seq=seq, mac=mac, key_id=key_id, ts=str(ts))


# ── Keys ──────────────────────────────────────────────────────────────────


class Keyring:
    """The active sealing key plus retired keys kept for verification only."""

    def __init__(self, active_id: str, keys: dict[str, bytes]) -> None:
        for key_id, key in keys.items():
            if not isinstance(key_id, str) or not _KEY_ID_RE.match(key_id):
                raise AuditError("audit key id must match [A-Za-z0-9_.-]{1,32}")
            if not isinstance(key, bytes) or len(key) < MIN_KEY_BYTES:
                raise AuditError(f"audit HMAC key {key_id!r} is shorter than {MIN_KEY_BYTES} bytes")
        if active_id not in keys:
            raise AuditError("active audit key id has no key")
        self._active_id = active_id
        self._keys = dict(keys)

    @property
    def active_id(self) -> str:
        return self._active_id

    def key(self, key_id: object) -> bytes:
        if not isinstance(key_id, str) or key_id not in self._keys:
            raise AuditError("unknown audit key id")
        return self._keys[key_id]

    def __repr__(self) -> str:  # never print key material
        return f"Keyring(active={self._active_id!r}, ids={sorted(self._keys)!r})"

    @classmethod
    def from_env(cls, env: dict | None = None) -> Keyring:
        """FONDACO_AUDIT_HMAC_KEY (required), _KEY_ID, _RETIRED_KEYS="id:key,…"."""
        env = os.environ if env is None else env
        raw = env.get(HMAC_KEY_ENV, "")
        if not raw:
            raise AuditError(f"{HMAC_KEY_ENV} is not set; refusing to run without an audit key")
        active_id = env.get(HMAC_KEY_ID_ENV, "").strip() or DEFAULT_KEY_ID
        keys = {active_id: raw.encode("utf-8")}
        for item in filter(
            None, (p.strip() for p in env.get(HMAC_RETIRED_KEYS_ENV, "").split(","))
        ):
            key_id, sep, key = item.partition(":")
            if not sep or not key:
                raise AuditError(f"{HMAC_RETIRED_KEYS_ENV} entries must be id:key")
            if key_id in keys:
                raise AuditError(f"duplicate audit key id {key_id!r}")
            keys[key_id] = key.encode("utf-8")
        return cls(active_id, keys)


# ── Sealing ───────────────────────────────────────────────────────────────


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def plan_sha256(plan: object) -> str:
    """Digest of a plan's canonical JSON — what the audit commits a plan to."""
    return hashlib.sha256(_canonical(plan)).hexdigest()


def _sealed_body(entry: dict) -> dict:
    return {
        "seq": entry["seq"],
        "ts": entry["ts"],
        "actor": entry["actor"],
        "event": entry["event"],
        "plan_sha256": entry["plan_sha256"],
        "payload": entry["payload"],
        "prev_mac": entry["prev_mac"],
        "key_id": entry["key_id"],
    }


def _mac(key: bytes, entry: dict) -> str:
    return hmac.new(key, _canonical(_sealed_body(entry)), hashlib.sha256).hexdigest()


# ── Storage backends ──────────────────────────────────────────────────────


class AuditBackend(Protocol):
    def read_all(self) -> list[dict]: ...

    def append(self, entry: dict) -> None: ...


class FileBackend:
    """JSONL file, opened in append mode only; one fsync per entry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with self.path.open(encoding="utf-8") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError) as exc:
            raise AuditError(f"unreadable: {type(exc).__name__}") from exc

    def append(self, entry: dict) -> None:
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())


class PostgresBackend:
    """Append-only table `fondaco_audit.fondaco_audit_log`.

    DDL: demo/dataset/init/04_audit_log.sql.

    The connecting role needs INSERT and SELECT only. UPDATE, DELETE and
    TRUNCATE are both ungranted and blocked by triggers, so the database
    refuses to rewrite history even for a role that was granted too much.
    Appends from concurrent writers are serialized by a transaction-scoped
    advisory lock, and an append whose seq is not exactly the next one is
    refused: a writer never chains onto a head it has not verified.
    """

    TABLE = "fondaco_audit.fondaco_audit_log"
    _LOCK_KEY = 0x46_4F_4E_44  # "FOND": any constant shared by all writers

    def __init__(self, dsn: str, connect_timeout_s: int = 10) -> None:
        self._dsn = dsn
        self._timeout = connect_timeout_s

    def _connect(self):
        import psycopg  # local import: the file backend must not need a driver

        try:
            return psycopg.connect(self._dsn, connect_timeout=self._timeout)
        except psycopg.Error as exc:
            raise AuditError(f"audit database unreachable: {type(exc).__name__}") from exc

    def read_all(self) -> list[dict]:
        import psycopg

        try:
            with self._connect() as conn:
                rows = conn.execute(f"SELECT seq, entry FROM {self.TABLE} ORDER BY seq").fetchall()  # noqa: S608 — constant identifier
        except psycopg.Error as exc:
            raise AuditError(f"audit read failed: {type(exc).__name__}") from exc
        entries = []
        for seq, raw in rows:
            try:
                entry = json.loads(raw)
            except ValueError as exc:
                raise AuditError("unreadable: JSONDecodeError") from exc
            if not isinstance(entry, dict) or entry.get("seq") != seq:
                raise AuditError("row seq does not match entry seq")
            entries.append(entry)
        return entries

    def append(self, entry: dict) -> None:
        import psycopg

        line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as conn, conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (self._LOCK_KEY,))
                row = conn.execute(f"SELECT max(seq) FROM {self.TABLE}").fetchone()  # noqa: S608
                current = -1 if row is None or row[0] is None else row[0]
                if current != entry["seq"] - 1:
                    raise AuditError("audit head moved: another writer appended")
                conn.execute(
                    f"INSERT INTO {self.TABLE} (seq, mac, entry) VALUES (%s, %s, %s)",  # noqa: S608
                    (entry["seq"], entry["mac"], line),
                )
        except psycopg.Error as exc:
            raise AuditError(f"audit append failed: {type(exc).__name__}") from exc


# ── Anchor sinks ──────────────────────────────────────────────────────────


class FileAnchorSink:
    """Appends each anchor as a JSON line to a file — put it on another volume/host."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def export(self, anchor: Anchor) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(anchor.to_dict(), sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise AuditError(f"anchor export failed: {type(exc).__name__}") from exc

    def latest(self) -> Anchor | None:
        if not self.path.exists():
            return None
        try:
            lines = [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            return Anchor.from_dict(json.loads(lines[-1])) if lines else None
        except (OSError, ValueError) as exc:
            raise AuditError(f"anchor file unreadable: {type(exc).__name__}") from exc


class WebhookAnchorSink:
    """POSTs (or PUTs, for an S3 presigned URL) each anchor as JSON over HTTPS."""

    def __init__(
        self, url: str, method: str = "POST", timeout_s: float = 10.0, transport=None
    ) -> None:
        if not url.startswith("https://"):
            raise AuditError("anchor webhook must be an https:// URL")
        if method not in ("POST", "PUT"):
            raise AuditError("anchor webhook method must be POST or PUT")
        self._url = url
        self._method = method
        self._timeout = timeout_s
        self._transport = transport

    def export(self, anchor: Anchor) -> None:
        import httpx

        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.request(self._method, self._url, json=anchor.to_dict())
            response.raise_for_status()
        except httpx.HTTPError as exc:
            # The URL can embed a presigned credential: never echo it.
            raise AuditError(f"anchor export failed: {type(exc).__name__}") from exc


# ── The log ───────────────────────────────────────────────────────────────


class AuditLog:
    def __init__(
        self,
        target: str | Path | AuditBackend,
        keyring: Keyring | None = None,
        anchor: Anchor | None = None,
    ) -> None:
        """Open (or create) a log and verify it before accepting any append.

        `target` is a path (JSONL backend) or a backend instance. `keyring`
        defaults to `Keyring.from_env()`. If `anchor` is given — typically the
        latest exported anchor — the log must still contain that exact entry,
        which detects tail truncation, a deleted log, and a rewrite even by
        someone holding the key.
        """
        self._backend: AuditBackend = (
            FileBackend(target) if isinstance(target, str | Path) else target
        )
        self._keyring = keyring if keyring is not None else Keyring.from_env()
        self._halted: str | None = None
        self._next_seq = 0
        self._prev_mac = GENESIS_MAC
        result = self.verify_chain()
        if not result.ok:
            raise AuditError(f"refusing to append to unverifiable log: {result.reason}")
        if anchor is not None and not self.verify_against_anchor(anchor).ok:
            raise AuditError(f"refusing to append: log does not match anchor seq {anchor.seq}")

    # -- writing -----------------------------------------------------------

    def append(
        self,
        event: str,
        payload: dict,
        actor: str = DEFAULT_ACTOR,
        plan: dict | None = None,
    ) -> dict:
        """Append one sealed entry. Raises AuditError instead of logging partially."""
        if self._halted is not None:
            raise AuditError(f"audit log halted: {self._halted}")
        self._check_head()
        entry = {
            "seq": self._next_seq,
            "ts": datetime.now(UTC).isoformat(),
            "actor": str(actor),
            "event": event,
            "plan_sha256": plan_sha256(plan) if plan is not None else None,
            "payload": payload,
            "prev_mac": self._prev_mac,
            "key_id": self._keyring.active_id,
        }
        try:
            entry["mac"] = _mac(self._keyring.key(entry["key_id"]), entry)
            json.dumps(entry, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AuditError(f"unserializable audit payload: {type(exc).__name__}") from exc
        self._backend.append(entry)
        self._next_seq += 1
        self._prev_mac = entry["mac"]
        return entry

    def _check_head(self) -> None:
        """The stored log must still end in, or extend, the head this process last saw.

        Fewer entries, or a different entry at our head position, means the log
        was truncated, deleted or rewritten underneath us: halt. More entries
        means another writer appended (Postgres backend): verify the whole
        chain before adopting its head, and halt if it does not verify.
        """
        entries = self._backend.read_all()
        n = self._next_seq
        intact = len(entries) >= n and (n == 0 or entries[n - 1].get("mac") == self._prev_mac)
        if not intact:
            self._halt("log changed underneath the writer (truncated, deleted or rewritten)")
            raise AuditError(f"audit log halted: {self._halted}")
        if len(entries) > n:
            result = self.verify_chain()
            if not result.ok:
                raise AuditError(f"audit log halted: {self._halted}")

    def _halt(self, reason: str) -> None:
        if self._halted is None:
            self._halted = reason

    # -- reading -----------------------------------------------------------

    def entries(self) -> list[dict]:
        """Read-only view of all entries, in append order."""
        return self._backend.read_all()

    @property
    def halted(self) -> str | None:
        """Why the log stopped accepting appends, or None while it is healthy."""
        return self._halted

    @property
    def key_id(self) -> str:
        return self._keyring.active_id

    def verify_chain(self) -> VerifyResult:
        """Recompute every MAC; halt the log on the first broken entry.

        A log that held entries when this process last saw it and now holds
        fewer — truncated, or deleted outright — fails as well: a missing
        chain is a broken chain.
        """
        result = self._verify()
        if not result.ok:
            self._halt(f"seq {result.bad_seq}: {result.reason}")
        elif result.entries < self._next_seq:
            result = VerifyResult(
                False, result.entries, bad_seq=result.entries, reason="log truncated or missing"
            )
            self._halt(result.reason)
        elif self._halted is None:
            # Adopt a head another writer appended (Postgres backend).
            self._adopt(result.entries)
        return result

    # The pre-HMAC name; kept so existing callers read naturally.
    verify = verify_chain

    def _adopt(self, count: int) -> None:
        if count == self._next_seq:
            return
        entries = self._backend.read_all()
        self._next_seq = len(entries)
        self._prev_mac = entries[-1]["mac"] if entries else GENESIS_MAC

    def _verify(self) -> VerifyResult:
        try:
            entries = self._backend.read_all()
        except AuditError as exc:
            return VerifyResult(False, 0, bad_seq=0, reason=str(exc))
        prev_mac = GENESIS_MAC
        for count, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
                reason = "legacy or malformed entry" if isinstance(entry, dict) else "malformed"
                return VerifyResult(False, count, bad_seq=count, reason=reason)
            if entry.get("seq") != count:
                return VerifyResult(False, count, bad_seq=count, reason="sequence break")
            if entry.get("prev_mac") != prev_mac:
                return VerifyResult(False, count, bad_seq=count, reason="chain break")
            try:
                key = self._keyring.key(entry.get("key_id"))
                expected = _mac(key, entry)
            except AuditError:
                return VerifyResult(False, count, bad_seq=count, reason="unknown key id")
            except (TypeError, ValueError):
                return VerifyResult(False, count, bad_seq=count, reason="malformed")
            claimed = entry.get("mac")
            if not isinstance(claimed, str) or not hmac.compare_digest(expected, claimed):
                return VerifyResult(False, count, bad_seq=count, reason="mac mismatch")
            prev_mac = claimed
        return VerifyResult(ok=True, entries=len(entries))

    # -- anchoring ---------------------------------------------------------

    def head(self) -> Anchor | None:
        """The current head as an anchor, or None for an empty log."""
        entries = self._backend.read_all()
        if not entries:
            return None
        last = entries[-1]
        return Anchor(seq=last["seq"], mac=last["mac"], key_id=last["key_id"], ts=last["ts"])

    def anchor(self, sink) -> Anchor | None:
        """Export the verified head to `sink`, then record the export in the log.

        Refuses to anchor a chain that does not verify: an anchor must only
        ever vouch for an intact history.
        """
        result = self.verify_chain()
        if not result.ok:
            raise AuditError(f"refusing to anchor a broken chain: {result.reason}")
        head = self.head()
        if head is None:
            return None
        sink.export(head)
        self.append(EVENT_ANCHOR_EXPORTED, {"anchored_seq": head.seq, "anchored_mac": head.mac})
        return head

    # The name the governance docs use for periodic anchoring (cron → sink).
    export_anchor = anchor

    def verify_against_anchor(self, anchor: Anchor) -> VerifyResult:
        """The chain verifies *and* still contains the anchored entry, unchanged."""
        result = self.verify_chain()
        if not result.ok:
            return result
        entries = self._backend.read_all()
        if anchor.seq >= len(entries):
            reason = "anchored entry missing (tail truncated or log replaced)"
        elif entries[anchor.seq].get("mac") != anchor.mac:
            reason = "anchored entry differs (log rewritten)"
        elif entries[anchor.seq].get("key_id") != anchor.key_id:
            reason = "anchored key id differs"
        else:
            return result
        self._halt(reason)
        return VerifyResult(False, len(entries), bad_seq=anchor.seq, reason=reason)


# ── Configuration ─────────────────────────────────────────────────────────


def audit_log_from_env(default_path: str = "./audit.jsonl") -> AuditLog:
    """Build the log the API uses from the environment. Raises AuditError (fail closed).

    FONDACO_AUDIT_BACKEND   file (default) | postgres
    FONDACO_AUDIT_LOG       JSONL path for the file backend
    FONDACO_AUDIT_DSN       connection string for the postgres backend
    FONDACO_AUDIT_ANCHOR_FILE  if set, the latest anchor in it must match at startup
    """
    backend_name = os.environ.get("FONDACO_AUDIT_BACKEND", "file").strip().lower()
    if backend_name == "file":
        backend: AuditBackend = FileBackend(os.environ.get("FONDACO_AUDIT_LOG", default_path))
    elif backend_name == "postgres":
        dsn = os.environ.get("FONDACO_AUDIT_DSN", "")
        if not dsn:
            raise AuditError("FONDACO_AUDIT_BACKEND=postgres requires FONDACO_AUDIT_DSN")
        backend = PostgresBackend(dsn)
    else:
        raise AuditError(f"unknown FONDACO_AUDIT_BACKEND: {backend_name!r}")
    anchor = None
    anchor_file = os.environ.get("FONDACO_AUDIT_ANCHOR_FILE", "")
    if anchor_file:
        anchor = FileAnchorSink(anchor_file).latest()
    return AuditLog(backend, anchor=anchor)


def anchor_sink_from_env():
    """The configured anchor sink, or None. Webhook wins over file when both are set."""
    url = os.environ.get("FONDACO_AUDIT_ANCHOR_WEBHOOK", "")
    if url:
        method = os.environ.get("FONDACO_AUDIT_ANCHOR_METHOD", "POST").strip().upper()
        return WebhookAnchorSink(url, method=method)
    path = os.environ.get("FONDACO_AUDIT_ANCHOR_FILE", "")
    if path:
        return FileAnchorSink(path)
    return None
