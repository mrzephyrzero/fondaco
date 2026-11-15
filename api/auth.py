# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Approver authentication for the approval UI.

The UI is where a human approves a plan, so it is the one surface an agent
must never be able to drive. This module makes it fail closed:

* **Approvers are named and hold a secret token.** `FONDACO_APPROVERS` lists
  `name:sha256hex` pairs (or `FONDACO_APPROVERS_FILE`, one pair per line).
  Only the SHA-256 of each token is configured; tokens are 256-bit random
  strings from `python -m api.auth new-approver <name>`, so a fast hash is
  enough. The approver recorded in the audit log is the authenticated name,
  never a form field.
* **No configuration is not "no auth".** With no approvers configured the app
  mints a one-time bootstrap token for approver `admin` and writes it to the
  server log only. Malformed configuration stops the app at startup.
* **Server-side sessions.** Login exchanges the token for a random session id
  in an HttpOnly, SameSite=Strict cookie; sessions expire, logout ends them,
  and a restart ends all of them.
* **CSRF.** Every state-changing UI request needs the session's CSRF token
  (form field or `X-CSRF-Token` header) and is refused when the browser says
  it is cross-site (`Origin` / `Sec-Fetch-Site`).
* **The agent's credential never opens the UI.** The `/api/v1` bearer token
  (`FONDACO_API_TOKEN`) is a different secret; startup refuses a configuration
  where it equals an approver token.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

UI_SESSION_COOKIE = "fondaco_ui"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
BOOTSTRAP_APPROVER = "admin"
DEFAULT_SESSION_TTL_S = 8 * 3600
MAX_SESSIONS = 10_000

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,63}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
# Hard ceiling on what a login form may carry, before any hashing.
MAX_TOKEN_LEN = 512


class AuthConfigError(ValueError):
    """Approver configuration is malformed: the app must not start."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def parse_approvers(text: str) -> dict[str, str]:
    """`name:sha256hex` entries separated by commas or newlines → {name: hash}.

    Blank entries and `#` comment lines are ignored. Anything else that does
    not parse is an error (fail closed), never silently skipped.
    """
    approvers: dict[str, str] = {}
    hashes: set[str] = set()
    for raw in re.split(r"[,\n]", text):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        name, sep, digest = entry.partition(":")
        name, digest = name.strip(), digest.strip().lower()
        if not sep or not _NAME_RE.match(name):
            raise AuthConfigError("approver entry must be 'name:sha256hex' with a plain name")
        if not _HASH_RE.match(digest):
            raise AuthConfigError(f"approver {name!r}: token hash must be 64 hex characters")
        if name in approvers:
            raise AuthConfigError(f"approver {name!r} is listed twice")
        if digest in hashes:
            raise AuthConfigError(f"approver {name!r} shares a token with another approver")
        approvers[name] = digest
        hashes.add(digest)
    return approvers


def approvers_from_env() -> dict[str, str] | None:
    """Configured approvers, or None when neither variable is set."""
    inline = os.environ.get("FONDACO_APPROVERS", "").strip()
    path = os.environ.get("FONDACO_APPROVERS_FILE", "").strip()
    if inline and path:
        raise AuthConfigError("set FONDACO_APPROVERS or FONDACO_APPROVERS_FILE, not both")
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise AuthConfigError(f"cannot read FONDACO_APPROVERS_FILE: {exc.strerror}") from None
        approvers = parse_approvers(text)
    elif inline:
        approvers = parse_approvers(inline)
    else:
        return None
    if not approvers:
        raise AuthConfigError("approver configuration is set but lists no approver")
    return approvers


def session_ttl_from_env() -> int:
    raw = os.environ.get("FONDACO_UI_SESSION_TTL_S", "").strip()
    if not raw:
        return DEFAULT_SESSION_TTL_S
    try:
        ttl = int(raw)
    except ValueError:
        return DEFAULT_SESSION_TTL_S
    # A malformed or non-positive value falls back; it never means "forever".
    return ttl if 60 <= ttl <= 7 * 24 * 3600 else DEFAULT_SESSION_TTL_S


def secure_cookie_from_env() -> bool:
    return os.environ.get("FONDACO_UI_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}


@dataclass
class Session:
    approver: str
    csrf: str
    expires: float


@dataclass
class ApproverAuth:
    """Approver registry plus the in-memory session store."""

    approvers: dict[str, str]
    session_ttl_s: int = DEFAULT_SESSION_TTL_S
    bootstrap: bool = False
    _sessions: dict[str, Session] = field(default_factory=dict)

    @classmethod
    def from_env(cls, api_token: str = "") -> ApproverAuth:
        approvers = approvers_from_env()
        auth = (
            cls(approvers, session_ttl_from_env())
            if approvers is not None
            else cls.with_bootstrap_token(session_ttl_from_env())
        )
        auth.check_separate_from(api_token)
        return auth

    @classmethod
    def with_bootstrap_token(cls, ttl: int = DEFAULT_SESSION_TTL_S) -> ApproverAuth:
        token = new_token()
        # The token goes to the server log only: whoever can read the server's
        # logs already controls the boundary. It dies with the process.
        print(
            f"[fondaco] No FONDACO_APPROVERS configured. Sign in to the approval UI as "
            f"{BOOTSTRAP_APPROVER!r} with this one-time token (valid until restart): {token}",
            file=sys.stderr,
            flush=True,
        )
        return cls({BOOTSTRAP_APPROVER: hash_token(token)}, ttl, bootstrap=True)

    def check_separate_from(self, api_token: str) -> None:
        """The agent's API token must never be an approver credential."""
        if api_token and hash_token(api_token) in self.approvers.values():
            raise AuthConfigError("FONDACO_API_TOKEN must differ from every approver token")

    def authenticate(self, token: str) -> str | None:
        """Approver name for a token, or None. Compares against every entry."""
        if not token or len(token) > MAX_TOKEN_LEN:
            return None
        digest = hash_token(token).encode()
        match = None
        for name, expected in self.approvers.items():
            if hmac.compare_digest(digest, expected.encode()):
                match = name
        return match

    def login(self, approver: str) -> str:
        """Open a session; returns the cookie value."""
        self._expire()
        if len(self._sessions) >= MAX_SESSIONS:
            # Bounded store: drop the oldest-expiring session rather than grow.
            oldest = min(self._sessions, key=lambda k: self._sessions[k].expires)
            del self._sessions[oldest]
        sid = secrets.token_urlsafe(32)
        self._sessions[hash_token(sid)] = Session(
            approver=approver,
            csrf=secrets.token_urlsafe(32),
            expires=time.monotonic() + self.session_ttl_s,
        )
        return sid

    def session(self, cookie: str | None) -> Session | None:
        if not cookie or len(cookie) > MAX_TOKEN_LEN:
            return None
        key = hash_token(cookie)
        current = self._sessions.get(key)
        if current is None:
            return None
        if current.expires <= time.monotonic():
            del self._sessions[key]
            return None
        return current

    def logout(self, cookie: str | None) -> None:
        if cookie and len(cookie) <= MAX_TOKEN_LEN:
            self._sessions.pop(hash_token(cookie), None)

    @staticmethod
    def csrf_ok(current: Session, supplied: str | None) -> bool:
        if not supplied or len(supplied) > MAX_TOKEN_LEN:
            return False
        return hmac.compare_digest(supplied.encode(), current.csrf.encode())

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, s in self._sessions.items() if s.expires <= now]:
            del self._sessions[key]


def cross_site(headers, host: str) -> bool:
    """True when the browser says the request comes from another site.

    `Sec-Fetch-Site` is authoritative when present; otherwise an `Origin`
    that is `null` or whose host differs from the request's `Host`. Requests without either
    (curl, test clients) are not cross-site by this test — they still need
    the session cookie and the CSRF token.
    """
    fetch_site = headers.get("sec-fetch-site", "").lower()
    if fetch_site:
        # Set by the browser itself, not by page script; it also covers the
        # browsers that send `Origin: null` on same-origin form posts.
        return fetch_site not in {"same-origin", "none"}
    origin = headers.get("origin")
    if origin is None:
        return False
    if origin == "null":
        return True
    _, _, rest = origin.partition("://")
    return rest.lower() != host.lower()


def safe_next(target: str | None) -> str:
    """A post-login redirect target that cannot leave the site."""
    if (
        not target
        or not target.startswith("/")
        or target.startswith("//")
        or "\\" in target
        or any(ord(c) < 0x20 for c in target)
    ):
        return "/"
    return target


def main(argv: list[str] | None = None) -> int:
    """`python -m api.auth new-approver <name>`: mint a token and its config entry."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "new-approver" or not _NAME_RE.match(args[1]):
        print("usage: python -m api.auth new-approver <name>", file=sys.stderr)
        return 2
    token = new_token()
    print(f"token (give it to {args[1]}, store nowhere else): {token}")
    print(f"FONDACO_APPROVERS entry: {args[1]}:{hash_token(token)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
