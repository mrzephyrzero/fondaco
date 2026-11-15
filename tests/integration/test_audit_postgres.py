# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
# ruff: noqa: S106, S608 — constant identifiers and demo-grade role passwords (see .env.example)
"""PostgreSQL audit backend: append-only enforced by the database itself.

Uses demo/dataset/init/04_audit_log.sql, loaded by the seeded_database
fixture. The writer connects as `fondaco_audit` (SELECT + INSERT only); the
tests also try to rewrite history as the table *owner*, which the triggers
refuse, and finally disable the triggers as owner — the database-admin
attacker — to show the HMAC seal still catches the edit.
"""

import pytest

from boundary.audit import (
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    AuditError,
    AuditLog,
    Keyring,
    PostgresBackend,
)
from tests.integration.conftest import ADMIN_DSN, requires_db

KEY = b"integration-audit-key-0123456789abcdef"
RING = Keyring("k1", {"k1": KEY})
TABLE = "fondaco_audit.fondaco_audit_log"


def _audit_dsn() -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(ADMIN_DSN, user="fondaco_audit", password="fondaco-audit-dev")


def _ro_dsn() -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(ADMIN_DSN, user="fondaco_ro", password="fondaco-ro-dev")


def _admin(sql: str, params=None):
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        return conn.execute(sql, params)


@pytest.fixture
def clean_audit_table(seeded_database):
    # The table is append-only even for its owner; emptying it between tests
    # takes disabling the user triggers, which only the owner can do.
    import psycopg

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"ALTER TABLE {TABLE} DISABLE TRIGGER USER")
        conn.execute(f"TRUNCATE {TABLE}")
        conn.execute(f"ALTER TABLE {TABLE} ENABLE TRIGGER USER")
    return PostgresBackend(_audit_dsn())


def _seed(backend) -> AuditLog:
    log = AuditLog(backend, RING)
    log.append(EVENT_QUESTION_RECEIVED, {"question": "q1"})
    log.append(EVENT_POLICY_DECISION, {"allow": True, "reason_code": "allow"})
    return log


@requires_db
def test_append_verify_and_reopen(clean_audit_table):
    _seed(clean_audit_table)
    reopened = AuditLog(clean_audit_table, RING)
    entry = reopened.append(EVENT_QUESTION_RECEIVED, {"question": "q2"})
    assert entry["seq"] == 2
    assert reopened.verify().ok is True
    assert [e["seq"] for e in reopened.entries()] == [0, 1, 2]


@requires_db
@pytest.mark.parametrize(
    "statement",
    [
        f"UPDATE {TABLE} SET entry = 'x' WHERE seq = 0",
        f"DELETE FROM {TABLE} WHERE seq = 1",
        f"TRUNCATE {TABLE}",
    ],
)
def test_owner_cannot_rewrite_history(clean_audit_table, statement):
    import psycopg

    _seed(clean_audit_table)
    with pytest.raises(psycopg.Error) as info:
        _admin(statement)
    assert "append-only" in str(info.value)
    assert AuditLog(clean_audit_table, RING).verify().entries == 2


@requires_db
@pytest.mark.parametrize(
    "statement",
    [
        f"UPDATE {TABLE} SET entry = 'x'",
        f"DELETE FROM {TABLE}",
        f"TRUNCATE {TABLE}",
    ],
)
def test_writer_role_lacks_the_privilege(clean_audit_table, statement):
    import psycopg

    _seed(clean_audit_table)
    with (
        psycopg.connect(_audit_dsn(), autocommit=True) as conn,
        pytest.raises(psycopg.Error) as info,
    ):
        conn.execute(statement)
    assert info.value.sqlstate == "42501"  # insufficient_privilege


@requires_db
def test_data_role_cannot_see_the_audit_table(clean_audit_table, adapter):
    import psycopg

    _seed(clean_audit_table)
    with psycopg.connect(_ro_dsn(), autocommit=True) as conn, pytest.raises(psycopg.Error) as info:
        conn.execute(f"SELECT count(*) FROM {TABLE}")
    assert info.value.sqlstate == "42501"
    # …and it never appears in the schema the planner is shown.
    assert "fondaco_audit_log" not in {t.name for t in adapter.get_schema().tables}


@requires_db
def test_admin_edit_with_triggers_disabled_is_caught_by_the_mac(clean_audit_table):
    import psycopg

    log = _seed(clean_audit_table)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"ALTER TABLE {TABLE} DISABLE TRIGGER USER")
        conn.execute(
            f"UPDATE {TABLE} SET entry = replace(entry, '\"allow\":true', '\"allow\":false') "
            "WHERE seq = 1"
        )
        conn.execute(f"ALTER TABLE {TABLE} ENABLE TRIGGER USER")
    result = log.verify_chain()
    assert result.ok is False
    assert result.bad_seq == 1
    with pytest.raises(AuditError):
        log.append(EVENT_QUESTION_RECEIVED, {"question": "after tamper"})


@requires_db
def test_two_writers_share_one_chain(clean_audit_table):
    first = _seed(clean_audit_table)
    second = AuditLog(PostgresBackend(_audit_dsn()), RING)
    second.append(EVENT_QUESTION_RECEIVED, {"question": "from second"})
    entry = first.append(EVENT_QUESTION_RECEIVED, {"question": "from first"})
    assert entry["seq"] == 3
    assert AuditLog(clean_audit_table, RING).verify().entries == 4


@requires_db
def test_stale_writer_append_is_refused_by_the_database_check(clean_audit_table):
    # Bypass the in-process head check and hand the backend an entry whose
    # seq is already taken: the locked max(seq) check refuses it.
    _seed(clean_audit_table)
    stale = {"seq": 1, "mac": "0" * 64}
    with pytest.raises(AuditError, match="head moved"):
        clean_audit_table.append(stale)


@requires_db
def test_unreachable_database_fails_closed():
    backend = PostgresBackend("postgresql://nobody:x@127.0.0.1:1/none", connect_timeout_s=1)
    with pytest.raises(AuditError, match="unverifiable"):
        AuditLog(backend, RING)
