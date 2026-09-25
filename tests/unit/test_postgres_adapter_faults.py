# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""PostgresAdapter faults that need no database: each surfaces as AdapterError.

Faults that only a live Postgres can produce — a failing catalog read, a
statement timeout, a statement with no result set — are in
tests/integration/test_postgres_adapter.py.
"""

import pytest

from executor.adapters.contract import AdapterError
from executor.adapters.postgres import PostgresAdapter

# Nothing listens on port 1, so the connection is refused at once. The user,
# password and database are distinctive so the tests can prove none of them
# is echoed back in the error.
UNREACHABLE = "postgresql://leaky_user:leaky_pw@127.0.0.1:1/leaky_db"


def _step(params=None):
    return {"id": "s1", "type": "query", "template": "SELECT 1", "params": params or {}}


def _assert_no_dsn_leak(message: str):
    for fragment in ("leaky_user", "leaky_pw", "leaky_db", "127.0.0.1"):
        assert fragment not in message


def test_unreachable_database_on_schema_read_is_a_connection_error():
    with pytest.raises(AdapterError) as excinfo:
        PostgresAdapter(UNREACHABLE).get_schema()
    assert excinfo.value.kind == "connection"
    assert excinfo.value.message.startswith("OperationalError (sqlstate=")
    _assert_no_dsn_leak(str(excinfo.value))


def test_unreachable_database_on_execute_is_a_connection_error():
    with pytest.raises(AdapterError) as excinfo:
        PostgresAdapter(UNREACHABLE).execute(_step())
    assert excinfo.value.kind == "connection"
    _assert_no_dsn_leak(str(excinfo.value))


def test_unconvertible_param_is_refused_before_any_connection():
    # The adapter is pointed at an unreachable database: had it tried to
    # connect, the kind would be "connection". "execution" proves the bad
    # param was caught first, and the offending value is not echoed.
    step = _step({"d": {"type": "date", "value": "not-a-date-4111"}})
    with pytest.raises(AdapterError) as excinfo:
        PostgresAdapter(UNREACHABLE).execute(step)
    assert excinfo.value.kind == "execution"
    assert excinfo.value.message == "invalid params: ValueError"
    assert "4111" not in str(excinfo.value)
