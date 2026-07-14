# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Integration fixtures: real Postgres, seeded with the demo dataset.

Skipped entirely unless both DSNs are set:
  FONDACO_TEST_DSN        — the read-only role (fondaco_ro)
  FONDACO_TEST_ADMIN_DSN  — an owner role able to create schema + data
CI provides a postgres service; locally, compose plus an override that
publishes the port works the same way.
"""

import os
from pathlib import Path

import pytest

RO_DSN = os.environ.get("FONDACO_TEST_DSN")
ADMIN_DSN = os.environ.get("FONDACO_TEST_ADMIN_DSN")

requires_db = pytest.mark.skipif(
    not (RO_DSN and ADMIN_DSN),
    reason="FONDACO_TEST_DSN / FONDACO_TEST_ADMIN_DSN not set",
)

_INIT_DIR = Path(__file__).resolve().parents[2] / "demo" / "dataset" / "init"


@pytest.fixture(scope="session")
def seeded_database():
    """Load demo/dataset/init/*.sql once (idempotent per test session)."""
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        already = conn.execute("SELECT to_regclass('public.orders')").fetchone()[0]
        if already is None:
            for sql_file in sorted(_INIT_DIR.glob("*.sql")):
                conn.execute(sql_file.read_text(encoding="utf-8"))
    return RO_DSN


@pytest.fixture(scope="session")
def adapter(seeded_database):
    from executor.adapters.postgres import PostgresAdapter

    return PostgresAdapter(seeded_database)
