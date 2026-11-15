# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""DuckDB adapter: a local, zero-install source, strictly read-only.

DuckDB runs inside the boundary's own process and, left at its defaults, can
read any file the process can (`FROM 'x.csv'`), install and load extensions
from the network, and change its own settings. Every one of those is turned
off, so the engine refuses them even if a template got past the boundary:

  * the database file is opened with `read_only=True` — no write of any kind;
  * `enable_external_access=false` — no file, URL or ATTACH, ever;
  * `autoinstall_known_extensions` / `autoload_known_extensions` /
    `allow_community_extensions` = false — nothing is fetched or loaded;
  * `lock_configuration=true` — none of the above can be changed by SET;
  * a per-statement timeout (DuckDB has none, so a timer interrupts the
    connection), a memory limit, a thread cap, and `max_rows` + 1 fetching.

CSV / Parquet / SQLite sources are loaded once, by `build_database`, into a
new DuckDB file through a separate connection that is closed before the
adapter opens the result read-only with external access disabled. The
boundary only ever hands this adapter single parsed SELECTs in the duckdb
dialect, and it still parses templates itself to label results.

Labels come from a reviewed schema file (`schema.yaml`, see
`executor/adapters/schema_file.py`); anything it does not label is
`restricted`. Driver messages are never passed through — only the class name.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import tempfile
import threading
from pathlib import Path

from boundary.policy import query_label
from executor.adapters.annotations import Annotations, apply_annotations
from executor.adapters.contract import (
    AdapterError,
    AnnotatedSchema,
    Capabilities,
    Column,
    LabeledResult,
    SchemaMetadataError,
    Table,
    schema_labels_dict,
)
from executor.adapters.params import ParamError, to_positional
from executor.adapters.schema_file import ColumnSpec, SchemaFile, TableSpec

DIALECT = "duckdb"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_LOADABLE = (".csv", ".tsv", ".parquet", ".sqlite", ".sqlite3", ".db")

# Every switch that would let the engine reach outside the opened file.
LOCKED_CONFIG: dict[str, object] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "allow_community_extensions": False,
    "allow_unsigned_extensions": False,
    "lock_configuration": True,
}

SCHEMA_SQL = """
SELECT c.table_name, c.column_name, c.data_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_catalog = c.table_catalog AND t.table_schema = c.table_schema
 AND t.table_name = c.table_name
WHERE c.table_schema = 'main' AND c.table_catalog = current_database()
  AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""
_SIZES_SQL = (
    "SELECT table_name, estimated_size FROM duckdb_tables() "
    "WHERE schema_name = 'main' AND database_name = current_database()"
)


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise AdapterError(
            "connection", "DuckDB adapter needs: pip install 'fondaco[duckdb]'"
        ) from exc
    return duckdb


def _digest(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> str:
    canonical = json.dumps([list(columns), [list(r) for r in rows]], default=str, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _table_name(path: Path) -> str:
    name = path.stem
    if not _IDENT_RE.match(name):
        raise AdapterError("connection", f"source file name is not a plain identifier: {name!r}")
    return name


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _load_sqlite(conn, path: Path, loaded: set[str]) -> None:
    """Copy every table of a SQLite file via stdlib sqlite3 (no DuckDB extension)."""
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = [
            r[0]
            for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in names:
            if not _IDENT_RE.match(name) or name in loaded:
                raise AdapterError("connection", f"sqlite table name not loadable: {name!r}")
            cursor = src.execute(f"SELECT * FROM {quote_ident(name)}")  # noqa: S608 — validated identifier
            header = [d[0] for d in cursor.description]
            with tempfile.NamedTemporaryFile(
                "w", suffix=".csv", newline="", encoding="utf-8", delete=False
            ) as tmp:
                writer = csv.writer(tmp)
                writer.writerow(header)
                writer.writerows(cursor)
            try:
                # Validated identifier; the path is a bound parameter.
                create = (
                    f"CREATE TABLE {quote_ident(name)} AS SELECT * FROM read_csv(?, header = true)"  # noqa: E501, S608
                )
                conn.execute(create, [tmp.name])
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            loaded.add(name)
    finally:
        src.close()


def build_database(target: str | Path, sources: list[str | Path]) -> Path:
    """Load CSV/TSV/Parquet/SQLite files into a new DuckDB file at `target`.

    Runs before the adapter exists, on a separate connection that is closed on
    return; the adapter then opens `target` read-only with external access off.
    One table per CSV/Parquet file (named after the file stem), every table of
    a SQLite file. `target` must not exist yet.
    """
    duckdb = _duckdb()
    target = Path(target)
    if target.exists():
        raise AdapterError("connection", "build target already exists")
    conn = duckdb.connect(str(target), config={"autoinstall_known_extensions": False})
    loaded: set[str] = set()
    try:
        for raw in sources:
            path = Path(raw)
            suffix = path.suffix.lower()
            if suffix not in _LOADABLE:
                raise AdapterError("connection", f"unsupported source type: {suffix or path.name}")
            if not path.is_file():
                raise AdapterError("connection", f"source file not found: {path.name}")
            if suffix in (".sqlite", ".sqlite3", ".db"):
                _load_sqlite(conn, path, loaded)
                continue
            name = _table_name(path)
            if name in loaded:
                raise AdapterError("connection", f"two sources map to table {name!r}")
            reader = "read_parquet(?)" if suffix == ".parquet" else "read_csv(?, header = true)"
            # Validated identifier and a fixed reader; the path is a bound parameter.
            create = f"CREATE TABLE {quote_ident(name)} AS SELECT * FROM {reader}"  # noqa: S608
            conn.execute(create, [str(path)])
            loaded.add(name)
    except AdapterError:
        conn.close()
        target.unlink(missing_ok=True)
        raise
    except Exception as exc:
        conn.close()
        target.unlink(missing_ok=True)
        raise AdapterError("connection", f"loading sources failed: {type(exc).__name__}") from exc
    conn.close()
    return target


class DuckDBAdapter:
    def __init__(
        self,
        database: str | Path,
        labels: SchemaFile | None = None,
        max_rows: int = 10_000,
        statement_timeout_s: float = 30,
        memory_limit: str = "1GB",
        threads: int = 2,
        annotations: Annotations | None = None,
    ) -> None:
        if not re.fullmatch(r"\d{1,6}(KB|MB|GB)", memory_limit):
            raise AdapterError("connection", "memory_limit must look like 512MB or 1GB")
        self._database = str(database)
        self._labels: SchemaFile = labels or {}
        self._max_rows = int(max_rows)
        self._timeout_s = max(0.1, float(statement_timeout_s))
        self._config = {**LOCKED_CONFIG, "memory_limit": memory_limit, "threads": max(1, threads)}
        self._annotations = annotations or {}

    def _open(self):
        duckdb = _duckdb()
        if not Path(self._database).is_file():
            raise AdapterError("connection", "database file not found")
        try:
            # Config is applied at open, before any statement, and locked.
            return duckdb.connect(self._database, read_only=True, config=dict(self._config))
        except duckdb.Error as exc:
            raise AdapterError("connection", type(exc).__name__) from exc

    def _column(self, name: str, sql_type: str, spec: ColumnSpec | None) -> Column:
        if spec is None:
            return Column(name, sql_type, "restricted")
        try:
            return Column(
                name,
                sql_type,
                spec.label,
                spec.comment,
                enum_values=spec.enum_values,
                business_description=spec.business_description,
            )
        except SchemaMetadataError:
            # Metadata the contract refuses is dropped; the label stands.
            return Column(name, sql_type, spec.label, spec.comment)

    def _table(self, name: str, columns: list[tuple[str, str]], rows: int) -> Table:
        spec = self._labels.get(name) or TableSpec()
        built = tuple(self._column(c, t, spec.columns.get(c)) for c, t in columns)
        try:
            table = Table(name, spec.label, built, spec.comment, rows)
        except SchemaMetadataError:
            # e.g. an internal column with enum values inside a confidential
            # table: strip every column's metadata, keep every label.
            plain = tuple(Column(c.name, c.sql_type, c.label, c.comment) for c in built)
            table = Table(name, spec.label, plain, spec.comment, rows)
        return apply_annotations(table, self._annotations)

    def _read_schema(self, conn) -> AnnotatedSchema:
        sizes = {name: int(size or 0) for name, size in conn.execute(_SIZES_SQL).fetchall()}
        tables: dict[str, list[tuple[str, str]]] = {}
        for table, column, sql_type in conn.execute(SCHEMA_SQL).fetchall():
            tables.setdefault(table, []).append((column, str(sql_type)))
        return AnnotatedSchema(
            tables=tuple(self._table(n, cols, sizes.get(n, 0)) for n, cols in tables.items())
        )

    def get_schema(self) -> AnnotatedSchema:
        duckdb = _duckdb()
        conn = self._open()
        try:
            return self._read_schema(conn)
        except duckdb.Error as exc:
            raise AdapterError("schema", type(exc).__name__) from exc
        finally:
            conn.close()

    def execute(self, step: dict) -> LabeledResult:
        if not isinstance(step, dict) or step.get("type") != "query":
            raise AdapterError("execution", "adapter only executes query steps")
        try:
            sql, values = to_positional(step["template"], step["params"])
        except (KeyError, TypeError, ValueError, ParamError) as exc:
            raise AdapterError("execution", f"invalid params: {type(exc).__name__}") from exc

        duckdb = _duckdb()
        conn = self._open()
        timed_out = threading.Event()

        def interrupt() -> None:
            timed_out.set()
            conn.interrupt()

        timer = threading.Timer(self._timeout_s, interrupt)
        try:
            labels = schema_labels_dict(self._read_schema(conn))
            label = query_label(step["template"], labels, dialect=DIALECT).name.lower()
            timer.start()
            cursor = conn.execute(sql, values)
            if cursor.description is None:
                raise AdapterError("execution", "statement returned no result set")
            columns = tuple(d[0] for d in cursor.description)
            fetched = cursor.fetchmany(self._max_rows + 1)
        except AdapterError:
            raise
        except duckdb.Error as exc:
            kind = "timeout" if timed_out.is_set() else "execution"
            raise AdapterError(kind, type(exc).__name__) from exc
        finally:
            timer.cancel()
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
