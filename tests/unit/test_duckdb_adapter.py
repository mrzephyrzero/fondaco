# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""DuckDB adapter: read-only, locked-down embedded engine, labels from a file.

These run against a real DuckDB engine (no mocks): the point is to prove the
engine itself refuses writes, file reads, extension loads and config changes
even when a statement reaches it without the boundary in front.
"""

import json
import sqlite3

import pytest

duckdb = pytest.importorskip("duckdb")

from executor.adapters.contract import AdapterError  # noqa: E402
from executor.adapters.duckdb_adapter import DuckDBAdapter, build_database  # noqa: E402
from executor.adapters.factory import AdapterConfigError, adapter_from_env  # noqa: E402
from executor.adapters.schema_file import SchemaFileError, load_schema_file  # noqa: E402

SCHEMA_YAML = """\
format: fondaco-annotations
version: 1
tables:
  - name: employees
    label: internal
    columns:
      - {name: id, label: internal}
      - {name: department, label: public, enum_values: [Sales, R&D, Ops]}
      - {name: role, label: internal, business_description: "Job title"}
      - {name: salary, label: confidential, enum_values: ["1", "2"]}
      - {name: iban, label: restricted}
  - name: secrets
    label: confidential
    columns:
      - {name: kind, label: internal, enum_values: [a, b]}
"""


def _query(template: str, params: dict | None = None) -> dict:
    return {"id": "s1", "type": "query", "template": template, "params": params or {}}


@pytest.fixture
def db(tmp_path):
    (tmp_path / "employees.csv").write_text(
        "id,department,role,salary,iban,full_name\n"
        "1,Sales,Manager,90000,IT60X0542811101000000123456,Ada Rossi\n"
        "2,R&D,Engineer,70000,IT60X0542811101000000654321,Bruno Neri\n"
        "3,Ops,Engineer,65000,IT60X0542811101000000111111,Carla Bianchi\n",
        encoding="utf-8",
    )
    lite = tmp_path / "extra.sqlite"
    with sqlite3.connect(lite) as conn:
        conn.execute("CREATE TABLE secrets (kind TEXT, token TEXT)")
        conn.execute("INSERT INTO secrets VALUES ('a', 'tok-1'), ('b', 'tok-2')")
    path = build_database(tmp_path / "demo.duckdb", [tmp_path / "employees.csv", lite])
    schema = tmp_path / "schema.yaml"
    schema.write_text(SCHEMA_YAML, encoding="utf-8")
    return path, load_schema_file(schema)


@pytest.fixture
def adapter(db):
    path, labels = db
    return DuckDBAdapter(path, labels=labels, max_rows=100, statement_timeout_s=2)


# ── Schema and labels ─────────────────────────────────────────────────────


def test_schema_carries_file_labels_and_restricts_the_rest(adapter):
    tables = {t.name: t for t in adapter.get_schema().tables}
    assert set(tables) == {"employees", "secrets"}
    emp = {c.name: c for c in tables["employees"].columns}
    assert tables["employees"].label == "internal"
    assert emp["department"].label == "public"
    assert emp["salary"].label == "confidential"
    assert emp["full_name"].label == "restricted"  # not in the file → restricted
    assert tables["employees"].row_count == 3


def test_enum_metadata_only_where_the_contract_allows(adapter):
    tables = {t.name: t for t in adapter.get_schema().tables}
    emp = {c.name: c for c in tables["employees"].columns}
    assert emp["department"].enum_values == ("Sales", "R&D", "Ops")
    assert emp["role"].business_description == "Job title"
    assert emp["salary"].enum_values is None  # confidential: dropped, label kept
    # internal column inside a confidential table: effective label is
    # confidential, so the values never reach the planner.
    secrets = {c.name: c for c in tables["secrets"].columns}
    assert secrets["kind"].enum_values is None
    assert secrets["kind"].label == "internal"
    assert tables["secrets"].label == "confidential"


def test_no_labels_file_means_everything_restricted(db):
    path, _ = db
    schema = DuckDBAdapter(path).get_schema()
    for table in schema.tables:
        assert table.label == "restricted"
        assert all(c.label == "restricted" for c in table.columns)


# ── Execution ─────────────────────────────────────────────────────────────


def test_execute_binds_params_and_labels_column_precisely(adapter):
    result = adapter.execute(
        _query(
            "SELECT department FROM employees WHERE role = %(r)s ORDER BY department",
            {"r": {"type": "string", "value": "Engineer"}},
        )
    )
    assert result.columns == ("department",)
    assert result.rows == (("Ops",), ("R&D",))
    assert result.label == "internal"  # table floor
    assert adapter.execute(_query("SELECT salary FROM employees")).label == "confidential"


def test_param_value_is_never_sql(adapter):
    result = adapter.execute(
        _query(
            "SELECT count(*) AS n FROM employees WHERE role = %(r)s",
            {"r": {"type": "string", "value": "x' OR '1'='1"}},
        )
    )
    assert result.rows == ((0,),)


def test_max_rows_is_enforced(db):
    path, labels = db
    small = DuckDBAdapter(path, labels=labels, max_rows=2)
    with pytest.raises(AdapterError) as info:
        small.execute(_query("SELECT id FROM employees"))
    assert info.value.kind == "limit_exceeded"


def test_runaway_query_is_interrupted(db):
    path, labels = db
    slow = DuckDBAdapter(path, labels=labels, statement_timeout_s=0.3)
    with pytest.raises(AdapterError) as info:
        slow.execute(_query("SELECT count(*) AS n FROM range(100000000000) a, range(10) b"))
    assert info.value.kind == "timeout"


# ── The engine itself refuses, with the boundary bypassed ─────────────────


@pytest.mark.parametrize(
    "template",
    [
        "SELECT * FROM 'employees.csv'",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_text('/etc/hostname')",
        "COPY employees TO 'out.csv'",
        "CREATE TABLE evil (a INTEGER)",
        "INSERT INTO employees (id) VALUES (99)",
        "DROP TABLE employees",
        "SET enable_external_access = true",
        "SET lock_configuration = false",
        "INSTALL httpfs",
        "LOAD httpfs",
        "ATTACH 'other.duckdb' AS other",
    ],
)
def test_engine_refuses_writes_files_extensions_and_config(adapter, template):
    with pytest.raises(AdapterError):
        adapter.execute(_query(template))


def test_engine_level_catalog_read_is_labelled_restricted(adapter):
    # The boundary denies this as unknown_relation; if it ever got through,
    # the adapter's own label would still be restricted.
    assert adapter.execute(_query("SELECT name FROM sqlite_master")).label == "restricted"


def test_driver_messages_never_reach_the_error(adapter):
    with pytest.raises(AdapterError) as info:
        adapter.execute(_query("SELECT CAST(iban AS INTEGER) AS x FROM employees"))
    assert "IT60" not in str(info.value)
    assert info.value.message.isidentifier()  # class name only


def test_database_file_is_not_modified(db, adapter):
    path, _ = db
    before = path.read_bytes()
    for template in ("CREATE TABLE evil (a INTEGER)", "DELETE FROM employees"):
        with pytest.raises(AdapterError):
            adapter.execute(_query(template))
    adapter.execute(_query("SELECT id FROM employees"))
    assert path.read_bytes() == before


def test_capabilities_are_read_only(adapter):
    assert adapter.capabilities().read_only is True


# ── Building from sources ─────────────────────────────────────────────────


def test_build_loads_parquet(tmp_path):
    parquet = tmp_path / "orders.parquet"
    conn = duckdb.connect()
    conn.execute(f"COPY (SELECT 1 AS id, 'OPEN' AS status) TO '{parquet}' (FORMAT parquet)")
    conn.close()
    path = build_database(tmp_path / "x.duckdb", [parquet])
    result = DuckDBAdapter(path).execute(_query("SELECT status FROM orders"))
    assert result.rows == (("OPEN",),)


@pytest.mark.parametrize("name", ["bad-name.csv", "notes.txt"])
def test_build_refuses_unusable_sources(tmp_path, name):
    src = tmp_path / name
    src.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(AdapterError):
        build_database(tmp_path / "x.duckdb", [src])
    assert not (tmp_path / "x.duckdb").exists()


def test_build_refuses_existing_target(tmp_path):
    target = tmp_path / "x.duckdb"
    target.write_bytes(b"")
    with pytest.raises(AdapterError):
        build_database(target, [])


def test_missing_database_file_is_a_connection_error(tmp_path):
    with pytest.raises(AdapterError) as info:
        DuckDBAdapter(tmp_path / "nope.duckdb").get_schema()
    assert info.value.kind == "connection"


# ── Schema file ───────────────────────────────────────────────────────────


def test_schema_file_json_and_unknown_labels(tmp_path):
    path = tmp_path / "schema.json"
    doc = {
        "format": "fondaco-annotations",
        "tables": [{"name": "t", "label": "PUBLIC", "columns": [{"name": "a", "label": "x"}]}],
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    labels = load_schema_file(path)
    assert labels["t"].label == "restricted"  # case matters: not a level
    assert labels["t"].columns["a"].label == "restricted"


@pytest.mark.parametrize(
    "text",
    [
        "format: something-else\ntables: []\n",
        "format: fondaco-annotations\ntables: {}\n",
        "format: fondaco-annotations\ntables:\n  - {name: t}\n  - {name: t}\n",
        "!!python/object/apply:os.system ['true']\n",
        "format: [unclosed\n",
    ],
)
def test_schema_file_refuses_malformed_documents(tmp_path, text):
    path = tmp_path / "schema.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SchemaFileError):
        load_schema_file(path)


# ── Factory ───────────────────────────────────────────────────────────────


def _env(**extra) -> dict:
    return {"FONDACO_ADAPTER": "duckdb", "FONDACO_SQL_DIALECT": "duckdb", **extra}


def test_factory_builds_from_path(db, tmp_path):
    path, _ = db
    (tmp_path / "s.yaml").write_text(SCHEMA_YAML, encoding="utf-8")
    env = _env(FONDACO_DUCKDB_PATH=str(path), FONDACO_DUCKDB_SCHEMA=str(tmp_path / "s.yaml"))
    assert isinstance(adapter_from_env(env=env), DuckDBAdapter)


def test_factory_builds_from_sources(tmp_path):
    csv = tmp_path / "employees.csv"
    csv.write_text("id,department\n1,Sales\n", encoding="utf-8")
    (tmp_path / "s.yaml").write_text(SCHEMA_YAML, encoding="utf-8")
    env = _env(FONDACO_DUCKDB_SOURCES=str(csv), FONDACO_DUCKDB_SCHEMA=str(tmp_path / "s.yaml"))
    result = adapter_from_env(env=env).execute(_query("SELECT department FROM employees"))
    assert result.rows == (("Sales",),)


@pytest.mark.parametrize(
    "extra",
    [
        {},  # no schema file
        {"FONDACO_DUCKDB_SCHEMA": "/nonexistent/schema.yaml", "FONDACO_DUCKDB_PATH": "x"},
        {"FONDACO_DUCKDB_SCHEMA": "SCHEMA"},  # neither path nor sources
        {
            "FONDACO_DUCKDB_SCHEMA": "SCHEMA",
            "FONDACO_DUCKDB_PATH": "a",
            "FONDACO_DUCKDB_SOURCES": "b",
        },
    ],
)
def test_factory_refuses_incomplete_config(tmp_path, extra):
    (tmp_path / "s.yaml").write_text(SCHEMA_YAML, encoding="utf-8")
    extra = {k: (str(tmp_path / "s.yaml") if v == "SCHEMA" else v) for k, v in extra.items()}
    with pytest.raises(AdapterConfigError):
        adapter_from_env(env=_env(**extra))


def test_factory_requires_duckdb_dialect(tmp_path):
    with pytest.raises(AdapterConfigError):
        adapter_from_env(env={"FONDACO_ADAPTER": "duckdb", "FONDACO_SQL_DIALECT": "postgres"})
