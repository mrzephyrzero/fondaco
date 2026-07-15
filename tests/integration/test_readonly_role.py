# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Checkpoint P2: the DB user provably cannot write — denial at the DB layer."""

import pytest

from tests.integration.conftest import requires_db

pytestmark = requires_db

WRITE_ATTEMPTS = [
    (
        "insert",
        "INSERT INTO orders (customer_id, region, status, order_date, total_amount) "
        "VALUES (1, 'north', 'pending', '2026-01-01', 1.0)",
    ),
    ("update", "UPDATE orders SET status = 'hacked' WHERE id = 1"),
    ("delete", "DELETE FROM orders WHERE id = 1"),
    ("create_table", "CREATE TABLE exfil (x text)"),
    ("drop_table", "DROP TABLE orders"),
    ("alter_table", "ALTER TABLE orders ADD COLUMN pwned text"),
]


@pytest.mark.parametrize(("name", "statement"), WRITE_ATTEMPTS, ids=[n for n, _ in WRITE_ATTEMPTS])
def test_readonly_role_cannot_write(seeded_database, name, statement):
    import psycopg

    with psycopg.connect(seeded_database, autocommit=True) as conn:
        with pytest.raises(psycopg.Error) as excinfo:
            conn.execute(statement)
        # Denied by the database, not by our code: privilege or read-only errors.
        assert excinfo.value.sqlstate in {"42501", "25006"}


def test_orders_row_count_untouched(seeded_database):
    import psycopg

    with psycopg.connect(seeded_database) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 20000
