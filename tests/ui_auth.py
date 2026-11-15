# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Test approvers for the authenticated approval UI.

`tests/conftest.py` configures these approvers for the whole suite (through
FONDACO_APPROVERS, exactly as a deployment would), so every app built by a
test requires a real sign-in. `sign_in` performs it and attaches the session's
CSRF token as a header, the way an htmx client would send it.
"""

from __future__ import annotations

import re

from api.auth import hash_token

TEST_APPROVERS = ("alice", "bob", "dana", "demo", "integration", "mallory", "attacker")


def approver_token(name: str) -> str:
    return f"test-only-approver-token-{name}-0123456789abcdef"


def approvers_env() -> str:
    return ",".join(f"{n}:{hash_token(approver_token(n))}" for n in TEST_APPROVERS)


_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def sign_in(client, name: str = "alice"):
    """Log `client` in as `name`; returns the client for chaining."""
    response = client.post("/login", data={"token": approver_token(name), "next": "/"})
    assert response.status_code == 200, response.text
    match = _CSRF_RE.search(response.text)
    assert match, "signed-in page carries no CSRF token"
    client.headers["X-CSRF-Token"] = match.group(1)
    return client
