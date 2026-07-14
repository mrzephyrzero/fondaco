# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Canary test on the REAL external API path (Checkpoint P3, cloud profile).

`test_canary.py` proves the same claim with a mock transport so CI needs no
secrets. This one proves it against a *live* cloud endpoint: the transport
wraps a real `httpx.HTTPTransport`, so the bytes inspected are the exact
serialized payload handed to the network — not an in-memory dict built
before serialization.

Skipped unless FONDACO_LLM_API_KEY is set; never runs in CI.
"""

import os

import httpx
import pytest

from tests.integration.conftest import ADMIN_DSN, requires_db

pytestmark = [
    requires_db,
    pytest.mark.skipif(
        not os.environ.get("FONDACO_LLM_API_KEY"),
        reason="FONDACO_LLM_API_KEY not set — live cloud path unavailable",
    ),
]

# High-entropy sentinels: strings a language model would never regenerate on
# its own, so a match can only mean the literal row value crossed the wire.
CANARIES = (
    "Z9QX7KWJ4PLUMBAT2VXQZZ8NROGGLE",
    "canary+7f3e2a91d4c6@zzq-sentinel-9x.invalid",
    "XR42-QVXX-WERGON-9931-THPLUZZ",
)


class RealCapturingTransport(httpx.BaseTransport):
    """Sends over the real network, recording each serialized request body."""

    def __init__(self) -> None:
        self._inner = httpx.HTTPTransport()
        self.serialized_bodies: list[bytes] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # request.content is the finalized, serialized body about to hit the
        # socket — this is the actual outbound network payload.
        self.serialized_bodies.append(request.content)
        return self._inner.handle_request(request)


@pytest.fixture
def planted_canaries(seeded_database):
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO customers (name, email, phone, city) VALUES (%s, %s, %s, %s)",
            (CANARIES[0], CANARIES[1], "+00 000000000", CANARIES[2]),
        )
    yield CANARIES
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM customers WHERE name = %s", (CANARIES[0],))


def test_no_canary_in_real_outbound_payload(adapter, planted_canaries):
    import psycopg

    from planner.client import PlannerClient, PlannerError

    # Sanity: the canary really is live data that a leak could carry.
    with psycopg.connect(ADMIN_DSN) as conn:
        found = conn.execute(
            "SELECT count(*) FROM customers WHERE email = %s", (CANARIES[1],)
        ).fetchone()[0]
    assert found == 1, "canary was not actually planted"

    capture = RealCapturingTransport()
    client = PlannerClient(
        base_url=os.environ.get("FONDACO_LLM_BASE_URL", "https://api.anthropic.com/v1"),
        api_key=os.environ["FONDACO_LLM_API_KEY"],
        model=os.environ.get("FONDACO_LLM_MODEL", "claude-sonnet-5"),
        max_attempts=2,
        timeout_s=90,
        sampling={},  # cloud profile omits temperature
        transport=capture,
    )

    schema = adapter.get_schema()
    outcome = "valid plan"
    try:
        plan, trace = client.generate_plan(
            "How many orders were placed per region since October 2025?", schema
        )
        outcome = f"valid plan in {len(trace.attempts)} attempt(s): " + ",".join(
            s["type"] for s in plan["steps"]
        )
    except PlannerError as exc:
        outcome = f"planner error {exc.code}"  # the outbound path still ran

    assert capture.serialized_bodies, "no request reached the network"
    haystack = b"\n".join(capture.serialized_bodies)

    print(
        f"\n=== live canary ===\n"
        f"endpoint   : {client._base_url} ({client._model})\n"  # noqa: SLF001 — evidence
        f"requests   : {len(capture.serialized_bodies)} serialized bodies on the real wire\n"
        f"bytes sent : {len(haystack)}\n"
        f"planner    : {outcome}\n"
        f"canaries   : {len(CANARIES)} planted in customers rows, "
        f"{sum(1 for c in CANARIES if c.encode() in haystack)} found outbound"
    )

    for canary in CANARIES:
        assert canary.encode() not in haystack, f"LEAK: {canary!r} crossed the boundary"
