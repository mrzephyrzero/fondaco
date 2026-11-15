# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Microsoft SQL Server adapter (pyodbc), strictly read-only.

Defense in depth, outermost first:
  * the login must hold only read permissions — and the adapter checks: on
    every connection it lists the login's effective permissions on the
    database, its schemas and its tables and views, and refuses to run
    anything if one of them is not a read (`_READ_PERMISSIONS`). This is the
    layer that actually enforces read-only. Verified against a live server:
    neither `ApplicationIntent=ReadOnly` nor ODBC's read-only access mode
    stops a write on a standalone instance, so a db_owner login would
    otherwise commit `INSERT …; COMMIT; SELECT 1` as handed to it;
  * the connection string is forced to `ApplicationIntent=ReadOnly`, which
    routes to a readable secondary in an Availability Group (it is only a
    routing hint everywhere else);
  * pyodbc opens the connection with `readonly=True` (SQL_ATTR_ACCESS_MODE),
    a hint the Microsoft driver does not enforce;
  * every statement runs inside a transaction that is always rolled back;
  * every session sets `LOCK_TIMEOUT` (a read never waits on a writer's lock
    for long), a query timeout, and READ COMMITTED isolation;
  * the boundary only ever hands this adapter single parsed SELECTs.

Labels come from the `MS_Description` extended property of each table and
column, in the same `label:<level> <description>` form the Postgres adapter
reads from comments; anything unlabelled is `restricted`. Plans use bare
table names, so the login's default schema must be the exposed schema: the
adapter checks `SCHEMA_NAME()` on every connection and refuses otherwise.
Driver messages are never passed through — only class name and SQLSTATE.
"""

from __future__ import annotations

import hashlib
import json
import re

from boundary.policy import query_label
from executor.adapters.annotations import Annotations, apply_annotations
from executor.adapters.contract import (
    AdapterError,
    AnnotatedSchema,
    Capabilities,
    Column,
    LabeledResult,
    Table,
    schema_labels_dict,
)
from executor.adapters.params import ParamError, to_positional

DIALECT = "tsql"
_LABEL_RE = re.compile(r"^label:(public|internal|confidential|restricted)\b\s*(.*)", re.DOTALL)
_TIMEOUT_SQLSTATES = {"HYT00", "HYT01"}
_LOCK_TIMEOUT_NATIVE = 1222

# Effective permissions a read-only login may hold. Allow-list, not deny-list:
# a permission SQL Server adds in a later release is refused until reviewed.
_READ_PERMISSIONS = frozenset({"CONNECT", "SELECT", "REFERENCES", "SHOWPLAN", "UNMASK"})

_PERMISSIONS_SQL = """
SELECT permission_name FROM fn_my_permissions(NULL, 'DATABASE')
UNION
SELECT p.permission_name FROM sys.schemas s
CROSS APPLY fn_my_permissions(QUOTENAME(s.name), 'SCHEMA') p
UNION
SELECT p.permission_name FROM sys.objects o
CROSS APPLY fn_my_permissions(
    QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name), 'OBJECT') p
WHERE o.type IN ('U', 'V') AND o.is_ms_shipped = 0
"""

_SCHEMA_SQL = """
SELECT t.name,
       CAST(tp.value AS nvarchar(4000)),
       COALESCE((SELECT SUM(p.rows) FROM sys.partitions p
                 WHERE p.object_id = t.object_id AND p.index_id IN (0, 1)), 0),
       c.name,
       TYPE_NAME(c.user_type_id),
       CAST(cp.value AS nvarchar(4000))
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
LEFT JOIN sys.extended_properties tp
       ON tp.class = 1 AND tp.major_id = t.object_id AND tp.minor_id = 0
      AND tp.name = 'MS_Description'
LEFT JOIN sys.extended_properties cp
       ON cp.class = 1 AND cp.major_id = t.object_id AND cp.minor_id = c.column_id
      AND cp.name = 'MS_Description'
WHERE s.name = ?
ORDER BY t.name, c.column_id
"""


def _parse_label(comment: object) -> tuple[str, str]:
    text = comment.strip() if isinstance(comment, str) else ""
    match = _LABEL_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return "restricted", text


def _force_read_only(conn_str: str) -> str:
    """Drop any ApplicationIntent the caller set, then append ReadOnly."""
    parts = [p for p in conn_str.split(";") if p.strip()]
    parts = [p for p in parts if p.split("=", 1)[0].strip().lower() != "applicationintent"]
    return ";".join([*parts, "ApplicationIntent=ReadOnly"]) + ";"


def _digest(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> str:
    canonical = json.dumps([list(columns), [list(r) for r in rows]], default=str, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sanitize(exc: Exception) -> str:
    """Class name + SQLSTATE only: driver text can embed data values."""
    args = getattr(exc, "args", ())
    sqlstate = args[0] if args and isinstance(args[0], str) and len(args[0]) == 5 else "unknown"
    return f"{type(exc).__name__} (sqlstate={sqlstate})"


def _write_permissions(names: list) -> list[str]:
    """Every permission that is not a plain read; empty means the login is read-only."""
    held = {str(n).upper() for n in names}
    return sorted(n for n in held if n not in _READ_PERMISSIONS and not n.startswith("VIEW "))


def _is_timeout(exc: Exception) -> bool:
    args = getattr(exc, "args", ())
    if args and args[0] in _TIMEOUT_SQLSTATES:
        return True
    text = str(args[1]) if len(args) > 1 else ""
    return f"({_LOCK_TIMEOUT_NATIVE})" in text


class SqlServerAdapter:
    def __init__(
        self,
        connection_string: str,
        schema: str = "dbo",
        max_rows: int = 10_000,
        statement_timeout_s: int = 30,
        lock_timeout_ms: int = 5_000,
        annotations: Annotations | None = None,
        connect=None,
    ) -> None:
        if not isinstance(schema, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", schema):
            raise AdapterError("connection", "schema name is not a plain identifier")
        self._conn_str = _force_read_only(connection_string)
        self._schema = schema
        self._max_rows = int(max_rows)
        self._timeout_s = max(1, int(statement_timeout_s))
        self._lock_timeout_ms = max(0, int(lock_timeout_ms))
        self._annotations = annotations or {}
        self._connect_fn = connect  # tests inject a fake; production uses pyodbc

    def _driver(self):
        if self._connect_fn is not None:
            return self._connect_fn, Exception
        try:
            import pyodbc
        except ImportError as exc:
            raise AdapterError(
                "connection", "SQL Server adapter needs: pip install 'fondaco[sqlserver]'"
            ) from exc
        return pyodbc.connect, pyodbc.Error

    def _open(self):
        connect, driver_error = self._driver()
        try:
            conn = connect(self._conn_str, autocommit=False, readonly=True, timeout=10)
            conn.timeout = self._timeout_s  # per-statement query timeout
            cur = conn.cursor()
            # Integers validated in __init__; SET does not take bound parameters.
            cur.execute(f"SET LOCK_TIMEOUT {self._lock_timeout_ms}")
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cur.execute("SET NOCOUNT ON")
            cur.execute("SELECT SCHEMA_NAME()")
            default_schema = cur.fetchone()[0]
            cur.execute(_PERMISSIONS_SQL)
            writes = _write_permissions([row[0] for row in cur.fetchall()])
        except AdapterError:
            raise
        except driver_error as exc:
            raise AdapterError("connection", _sanitize(exc)) from exc
        if writes:
            conn.close()
            shown = ", ".join(writes[:5]) + (", ..." if len(writes) > 5 else "")
            raise AdapterError(
                "connection", f"login is not read-only (holds {shown}); grant it SELECT only"
            )
        if default_schema != self._schema:
            conn.close()
            raise AdapterError(
                "connection",
                "login default schema differs from the exposed schema; bare names would "
                "resolve elsewhere",
            )
        return conn, cur, driver_error

    def _read_schema(self, cur) -> AnnotatedSchema:
        cur.execute(_SCHEMA_SQL, self._schema)
        tables: dict[str, dict] = {}
        for table, t_comment, row_count, column, sql_type, c_comment in cur.fetchall():
            entry = tables.setdefault(
                table, {"comment": t_comment, "rows": int(row_count or 0), "columns": []}
            )
            label, desc = _parse_label(c_comment)
            entry["columns"].append(Column(column, str(sql_type), label, desc))
        built = []
        for name, entry in tables.items():
            label, desc = _parse_label(entry["comment"])
            table = Table(name, label, tuple(entry["columns"]), desc, entry["rows"])
            built.append(apply_annotations(table, self._annotations))
        return AnnotatedSchema(tables=tuple(built))

    def get_schema(self) -> AnnotatedSchema:
        conn, cur, driver_error = self._open()
        try:
            return self._read_schema(cur)
        except driver_error as exc:
            raise AdapterError("schema", _sanitize(exc)) from exc
        finally:
            conn.close()

    def execute(self, step: dict) -> LabeledResult:
        if not isinstance(step, dict) or step.get("type") != "query":
            raise AdapterError("execution", "adapter only executes query steps")
        try:
            sql, values = to_positional(step["template"], step["params"])
        except (KeyError, TypeError, ValueError, ParamError) as exc:
            raise AdapterError("execution", f"invalid params: {type(exc).__name__}") from exc

        conn, cur, driver_error = self._open()
        try:
            # Labels and rows from the same connection and transaction.
            labels = schema_labels_dict(self._read_schema(cur))
            label = query_label(step["template"], labels, dialect=DIALECT).name.lower()
            cur.execute(sql, *values)
            if cur.description is None:
                raise AdapterError("execution", "statement returned no result set")
            columns = tuple(d[0] for d in cur.description)
            fetched = cur.fetchmany(self._max_rows + 1)
        except AdapterError:
            raise
        except driver_error as exc:
            kind = "timeout" if _is_timeout(exc) else "execution"
            raise AdapterError(kind, _sanitize(exc)) from exc
        finally:
            try:
                conn.rollback()  # nothing to keep: a read leaves no transaction behind
            finally:
                conn.close()

        if len(fetched) > self._max_rows:
            raise AdapterError("limit_exceeded", f"result exceeds max_rows={self._max_rows}")
        rows = tuple(tuple(r) for r in fetched)
        return LabeledResult(columns, rows, label, len(rows), _digest(columns, rows))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            dsl_versions=("v0",),
            param_types=("string", "int", "float", "bool", "date", "timestamp"),
            max_rows=self._max_rows,
            read_only=True,
        )
