# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""SQL Server adapter against a real SQL Server, not a fake pyodbc.

Opt-in: skipped unless FONDACO_TEST_MSSQL_ADMIN holds an ODBC connection
string for an admin login (no DATABASE= part), and pyodbc plus an ODBC
driver are installed. The fixture creates the database `fondaco_it`, the
schema `sales` and two logins — one granted SELECT only, one db_owner — so
the read-only guarantees are checked against what the server actually does.

    docker run -d -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=Fondaco-dev-1 \\
        -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest

then set FONDACO_TEST_MSSQL_ADMIN as `.github/workflows/live-adapters.yml`
does and run `pytest tests/integration/test_sqlserver_live.py`.
"""

import os
import re
import threading
from pathlib import Path

import pytest

from executor.adapters.contract import AdapterError

ADMIN = os.environ.get("FONDACO_TEST_MSSQL_ADMIN")
pytestmark = pytest.mark.skipif(not ADMIN, reason="FONDACO_TEST_MSSQL_ADMIN not set")

DATABASE = "fondaco_it"
SCHEMA = "sales"
RO_LOGIN, RO_PASSWORD = "fondaco_it_ro", "Fondaco-it-ro-1"  # noqa: S105 — throwaway test login
RW_LOGIN, RW_PASSWORD = "fondaco_it_rw", "Fondaco-it-rw-1"  # noqa: S105 — throwaway test login
_SEED = Path(__file__).with_name("mssql_seed.sql")


def _step(template, params=None):
    return {"id": "s1", "type": "query", "template": template, "params": params or {}}


def _conn_str(login: str, password: str) -> str:
    base = ";".join(
        p for p in ADMIN.split(";") if p.split("=", 1)[0].strip().upper() not in ("UID", "PWD")
    )
    return f"{base};DATABASE={DATABASE};UID={login};PWD={password}"


def _admin(database: str | None = DATABASE, autocommit: bool = True):
    import pyodbc

    suffix = f";DATABASE={database}" if database else ""
    return pyodbc.connect(ADMIN + suffix, autocommit=autocommit)


@pytest.fixture(scope="module")
def server():
    pytest.importorskip("pyodbc")
    with _admin(None) as conn:
        conn.execute(f"IF DB_ID('{DATABASE}') IS NULL CREATE DATABASE {DATABASE}")
        for login, password in ((RO_LOGIN, RO_PASSWORD), (RW_LOGIN, RW_PASSWORD)):
            conn.execute(
                f"IF SUSER_ID('{login}') IS NULL "
                f"CREATE LOGIN {login} WITH PASSWORD = '{password}', CHECK_POLICY = OFF"
            )
    with _admin() as conn:
        for batch in re.split(r"(?m)^GO\s*$", _SEED.read_text(encoding="utf-8")):
            if batch.strip():
                conn.execute(batch)
        for login in (RO_LOGIN, RW_LOGIN):
            conn.execute(
                f"IF USER_ID('{login}') IS NULL "
                f"CREATE USER {login} FOR LOGIN {login} WITH DEFAULT_SCHEMA = {SCHEMA}"
            )
        conn.execute(f"GRANT SELECT ON SCHEMA::{SCHEMA} TO {RO_LOGIN}")
        conn.execute(f"ALTER ROLE db_owner ADD MEMBER {RW_LOGIN}")
    return _conn_str(RO_LOGIN, RO_PASSWORD)


def _adapter(conn_str, **kwargs):
    from executor.adapters.sqlserver import SqlServerAdapter

    return SqlServerAdapter(conn_str, schema=SCHEMA, **kwargs)


def _product_ids() -> list[int]:
    with _admin() as conn:
        return [r[0] for r in conn.execute("SELECT id FROM sales.products ORDER BY id")]


# ── Schema and labels ───────────────────────────────────────────────────────


def test_schema_labels_read_from_ms_description(server):
    tables = {t.name: t for t in _adapter(server).get_schema().tables}
    assert set(tables) == {"products", "orders", "customers", "numbers"}
    assert tables["products"].label == "public"
    assert tables["products"].comment == "Product catalogue"
    assert tables["orders"].label == "internal"
    columns = {c.name: c for c in tables["orders"].columns}
    assert columns["amount"].label == "confidential"
    assert columns["amount"].comment == "Revenue"
    # customers.phone carries no MS_Description: it must fall back to restricted.
    customers = {c.name: c for c in tables["customers"].columns}
    assert customers["phone"].label == "restricted"
    assert customers["city"].label == "confidential"
    assert tables["numbers"].row_count == 10_000  # from sys.partitions, never row data


def test_execute_binds_params_and_labels_column_precisely(server):
    adapter = _adapter(server)
    result = adapter.execute(
        _step(
            "SELECT region, status FROM orders WHERE order_date >= %(since)s ORDER BY id",
            {"since": {"type": "date", "value": "2025-06-01"}},
        )
    )
    assert result.columns == ("region", "status")
    assert result.rows == (("north", "open"), ("south", "shipped"), ("east", "open"))
    assert result.label == "internal"
    assert adapter.execute(_step("SELECT amount FROM orders")).label == "confidential"
    assert adapter.execute(_step("SELECT city, phone FROM customers")).label == "restricted"


def test_a_param_is_bound_not_spliced(server):
    hostile = "north' OR '1'='1"
    result = _adapter(server).execute(
        _step(
            "SELECT id FROM orders WHERE region = %(r)s",
            {"r": {"type": "string", "value": hostile}},
        )
    )
    assert result.row_count == 0


# ── Read-only enforcement ───────────────────────────────────────────────────


def test_select_only_login_cannot_write(server):
    before = _product_ids()
    with pytest.raises(AdapterError) as excinfo:
        _adapter(server).execute(_step("INSERT INTO products VALUES (90, N'x', N'y')"))
    assert excinfo.value.kind == "execution"
    assert _product_ids() == before


@pytest.mark.parametrize(
    "template",
    [
        "INSERT INTO products VALUES (91, N'x', N'y')",
        "INSERT INTO products OUTPUT inserted.id VALUES (92, N'x', N'y')",
        # The case that used to get through: the explicit COMMIT outruns the
        # adapter's rollback, and the trailing SELECT supplies a result set.
        "INSERT INTO products VALUES (93, N'x', N'y'); COMMIT; SELECT 1 AS one",
    ],
)
def test_login_that_can_write_is_refused_before_anything_runs(server, template):
    # ApplicationIntent=ReadOnly and pyodbc's readonly=True are hints that a
    # standalone server ignores; the adapter must not lean on them. It checks
    # the login's effective permissions instead and refuses to run at all.
    before = _product_ids()
    writer = _adapter(_conn_str(RW_LOGIN, RW_PASSWORD))
    with pytest.raises(AdapterError) as excinfo:
        writer.execute(_step(template))
    assert excinfo.value.kind == "connection"
    assert "not read-only" in excinfo.value.message
    assert _product_ids() == before


def test_a_single_object_grant_is_enough_to_refuse(server):
    with _admin() as conn:
        conn.execute(f"GRANT UPDATE ON sales.orders TO {RO_LOGIN}")
    try:
        with pytest.raises(AdapterError) as excinfo:
            _adapter(server).get_schema()
        assert excinfo.value.kind == "connection"
        assert "UPDATE" in excinfo.value.message
    finally:
        with _admin() as conn:
            conn.execute(f"REVOKE UPDATE ON sales.orders FROM {RO_LOGIN}")
    assert _adapter(server).get_schema().tables  # read-only again → accepted


def test_wrong_default_schema_is_refused(server):
    from executor.adapters.sqlserver import SqlServerAdapter

    with pytest.raises(AdapterError) as excinfo:
        SqlServerAdapter(server, schema="dbo").get_schema()
    assert excinfo.value.kind == "connection"
    assert "default schema" in excinfo.value.message


# ── Limits and timeouts ─────────────────────────────────────────────────────


def test_max_rows_is_exact(server):
    assert _adapter(server, max_rows=10_000).execute(_step("SELECT n FROM numbers")).row_count == (
        10_000
    )
    with pytest.raises(AdapterError) as excinfo:
        _adapter(server, max_rows=9_999).execute(_step("SELECT n FROM numbers"))
    assert excinfo.value.kind == "limit_exceeded"


def test_query_timeout_is_a_timeout_error(server):
    # 10^12 rows to count: far beyond a one-second budget. The driver cancels
    # the statement client-side and reports SQLSTATE HYT00.
    slow = _adapter(server, statement_timeout_s=1)
    with pytest.raises(AdapterError) as excinfo:
        slow.execute(_step("SELECT COUNT_BIG(*) AS c FROM numbers a, numbers b, numbers c"))
    assert excinfo.value.kind == "timeout"
    assert excinfo.value.message == "OperationalError (sqlstate=HYT00)"


def test_lock_timeout_is_a_timeout_error(server):
    # A writer holds a row lock; the read must give up after LOCK_TIMEOUT
    # (error 1222) instead of waiting out the whole statement timeout.
    writer = _admin(autocommit=False)
    try:
        writer.execute("UPDATE sales.orders SET status = N'held' WHERE id = 1")
        reader = _adapter(server, lock_timeout_ms=300, statement_timeout_s=20)
        done = threading.Event()
        errors = []

        def read():
            try:
                reader.execute(_step("SELECT status FROM orders"))
            except AdapterError as exc:
                errors.append(exc)
            done.set()

        threading.Thread(target=read, daemon=True).start()
        assert done.wait(10), "the read waited on the writer's lock past LOCK_TIMEOUT"
        assert [e.kind for e in errors] == ["timeout"]
    finally:
        writer.rollback()
        writer.close()


# ── Errors ──────────────────────────────────────────────────────────────────


def test_driver_text_never_passes_through(server):
    secret = "customer-secret-value"  # noqa: S105 — canary param value, not a credential
    with pytest.raises(AdapterError) as excinfo:
        _adapter(server).execute(
            _step(
                "SELECT missing_column FROM orders WHERE region = %(r)s",
                {"r": {"type": "string", "value": secret}},
            )
        )
    assert excinfo.value.kind == "execution"
    assert excinfo.value.message == "ProgrammingError (sqlstate=42S22)"


def test_bad_password_is_a_sanitized_connection_error(server):
    with pytest.raises(AdapterError) as excinfo:
        _adapter(_conn_str(RO_LOGIN, "wrong-password")).get_schema()
    assert excinfo.value.kind == "connection"
    assert "wrong-password" not in excinfo.value.message
    assert RO_LOGIN not in excinfo.value.message
