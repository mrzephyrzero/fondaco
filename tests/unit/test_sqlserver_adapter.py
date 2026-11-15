# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""SQL Server adapter against a fake pyodbc: read-only wiring, labels, limits, errors."""

from datetime import date

import pytest

from executor.adapters.contract import AdapterError
from executor.adapters.sqlserver import SqlServerAdapter, _force_read_only

SCHEMA_ROWS = [
    ("orders", "label:internal Orders", 120, "region", "nvarchar", "label:internal"),
    ("orders", "label:internal Orders", 120, "status", "nvarchar", "label:internal"),
    ("orders", "label:internal Orders", 120, "margin", "decimal", "label:confidential"),
    ("employees", "label:internal", 10, "salary", "decimal", "label:restricted Payroll"),
    ("employees", "label:internal", 10, "notes", "nvarchar", "free text, no label"),
    ("audit_tmp", None, 0, "x", "int", None),
]


class FakeError(Exception):
    pass


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def execute(self, sql, *params):
        self.conn.statements.append((sql, params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise self.conn.error
        if sql == "SELECT SCHEMA_NAME()":
            self._rows = [(self.conn.default_schema,)]
        elif "fn_my_permissions" in sql:
            self._rows = [(p,) for p in self.conn.permissions]
        elif "sys.tables" in sql:
            self._rows = list(SCHEMA_ROWS)
        elif sql.startswith("SET"):
            self._rows = []
        else:
            self.description = (("region",), ("n",))
            self._rows = list(self.conn.result_rows)
        return self

    def fetchone(self):
        return self._rows[0]

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, n):
        return list(self._rows[:n])


class FakeConn:
    def __init__(
        self,
        default_schema="dbo",
        result_rows=(),
        fail_on=None,
        error=None,
        permissions=("CONNECT", "SELECT", "VIEW ANY COLUMN MASTER KEY DEFINITION"),
    ):
        self.default_schema = default_schema
        self.permissions = permissions
        self.result_rows = result_rows
        self.fail_on = fail_on
        self.error = error
        self.statements = []
        self.timeout = None
        self.closed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _adapter(conn=None, **kwargs):
    conn = conn or FakeConn(result_rows=[("north", 7), ("south", 5)])
    calls = []

    def connect(conn_str, **kw):
        calls.append((conn_str, kw))
        return conn

    adapter = SqlServerAdapter("Driver={ODBC Driver 18};Server=db;", connect=connect, **kwargs)
    return adapter, conn, calls


def _step(template="SELECT region, count(*) AS n FROM orders GROUP BY region", params=None):
    return {"id": "s1", "type": "query", "template": template, "params": params or {}}


def test_connection_is_forced_read_only_with_timeouts():
    adapter, conn, calls = _adapter(statement_timeout_s=12, lock_timeout_ms=3000)
    adapter.get_schema()
    conn_str, kwargs = calls[0]
    assert conn_str.endswith("ApplicationIntent=ReadOnly;")
    assert kwargs["readonly"] is True and kwargs["autocommit"] is False
    assert conn.timeout == 12
    sqls = [s for s, _ in conn.statements]
    assert "SET LOCK_TIMEOUT 3000" in sqls
    assert "SET TRANSACTION ISOLATION LEVEL READ COMMITTED" in sqls
    assert conn.closed


def test_caller_cannot_override_application_intent():
    forced = _force_read_only("Server=db;ApplicationIntent=ReadWrite;applicationintent = x")
    assert forced.count("ApplicationIntent") == 1
    assert forced.endswith("ApplicationIntent=ReadOnly;")


def test_labels_from_extended_properties_and_restricted_fallback():
    adapter, _, _ = _adapter()
    tables = {t.name: t for t in adapter.get_schema().tables}
    assert tables["orders"].label == "internal" and tables["orders"].row_count == 120
    cols = {c.name: c.label for c in tables["employees"].columns}
    assert cols == {"salary": "restricted", "notes": "restricted"}
    assert tables["audit_tmp"].label == "restricted"


def test_wrong_default_schema_is_refused():
    adapter, conn, _ = _adapter(FakeConn(default_schema="sales"))
    with pytest.raises(AdapterError) as err:
        adapter.get_schema()
    assert err.value.kind == "connection"
    assert conn.closed


@pytest.mark.parametrize(
    "permissions",
    [
        ("CONNECT", "SELECT", "INSERT"),
        ("CONNECT", "SELECT", "CONTROL"),
        ("CONNECT", "EXECUTE"),
        ("CONNECT", "SELECT", "SOME PERMISSION ADDED IN A LATER RELEASE"),
    ],
)
def test_login_holding_anything_but_reads_is_refused(permissions):
    # ApplicationIntent=ReadOnly and readonly=True do not stop a write on a
    # standalone server (shown live in test_sqlserver_live.py); the
    # permission check is what enforces read-only, and it is an allow-list.
    adapter, conn, _ = _adapter(FakeConn(permissions=permissions))
    with pytest.raises(AdapterError) as err:
        adapter.execute(_step())
    assert err.value.kind == "connection"
    assert "not read-only" in err.value.message
    assert conn.closed
    assert not any(s.startswith("SELECT region") for s, _ in conn.statements)


def test_read_permissions_are_accepted():
    reads = ("CONNECT", "SELECT", "REFERENCES", "SHOWPLAN", "UNMASK", "VIEW DEFINITION")
    adapter, _, _ = _adapter(FakeConn(permissions=reads, result_rows=[("north", 1)]))
    assert adapter.execute(_step()).row_count == 1


def test_execute_binds_positional_params_and_labels():
    adapter, conn, _ = _adapter()
    result = adapter.execute(
        _step(
            "SELECT region, count(*) AS n FROM orders WHERE status = %(s)s AND region <> %(r)s "
            "AND status <> %(s)s GROUP BY region",
            {"s": {"type": "string", "value": "paid"}, "r": {"type": "string", "value": "x"}},
        )
    )
    sql, params = conn.statements[-1]
    assert "%(" not in sql and sql.count("?") == 3
    assert params == ("paid", "x", "paid")
    assert result.columns == ("region", "n") and result.row_count == 2
    assert result.label == "internal"
    assert conn.rolled_back and conn.closed


def test_label_is_column_precise_in_tsql():
    adapter, _, _ = _adapter()
    assert adapter.execute(_step("SELECT region FROM orders WHERE margin > 1")).label == (
        "confidential"
    )
    assert adapter.execute(_step("SELECT TOP 5 region FROM orders")).label == "internal"


def test_date_params_are_converted():
    adapter, conn, _ = _adapter()
    adapter.execute(
        _step(
            "SELECT region FROM orders WHERE status > %(d)s",
            {"d": {"type": "date", "value": "2026-01-31"}},
        )
    )
    assert conn.statements[-1][1] == (date(2026, 1, 31),)


def test_row_overflow_errors_rather_than_truncates():
    adapter, _, _ = _adapter(FakeConn(result_rows=[("a", 1)] * 6), max_rows=5)
    with pytest.raises(AdapterError) as err:
        adapter.execute(_step())
    assert err.value.kind == "limit_exceeded"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (FakeError("HYT00", "[HYT00] Query timeout expired"), "timeout"),
        (FakeError("HY000", "Lock request time out period exceeded. (1222)"), "timeout"),
        (FakeError("22018", "Conversion failed for value 'alice@example.it'"), "execution"),
    ],
)
def test_errors_are_sanitized(error, kind):
    conn = FakeConn(fail_on="FROM orders", error=error)
    adapter, _, _ = _adapter(conn)
    with pytest.raises(AdapterError) as err:
        adapter.execute(_step())
    assert err.value.kind == kind
    assert "alice" not in str(err.value) and "Conversion" not in str(err.value)
    assert conn.closed


def test_only_query_steps_and_declared_params():
    adapter, conn, _ = _adapter()
    with pytest.raises(AdapterError):
        adapter.execute({"id": "s2", "type": "aggregate"})
    with pytest.raises(AdapterError):
        adapter.execute(_step("SELECT region FROM orders WHERE status = %(nope)s"))
    assert conn.statements == []  # refused before connecting


def test_schema_name_must_be_an_identifier():
    with pytest.raises(AdapterError):
        SqlServerAdapter("Server=db;", schema="dbo; DROP TABLE x")


def test_missing_driver_is_a_connection_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_pyodbc(name, *args, **kwargs):
        if name == "pyodbc":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pyodbc)
    with pytest.raises(AdapterError) as err:
        SqlServerAdapter("Server=db;").get_schema()
    assert "fondaco[sqlserver]" in str(err.value)


def test_capabilities_are_read_only():
    adapter, _, _ = _adapter()
    assert adapter.capabilities().read_only is True
