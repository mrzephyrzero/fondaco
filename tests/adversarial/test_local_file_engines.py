# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Escapes specific to embedded engines (DuckDB, SQLite).

An embedded engine runs inside the boundary's own process and can read the
host filesystem: DuckDB scans `FROM 'file.csv'`, `read_csv(...)`, `glob(...)`;
SQLite exposes `sqlite_master`, `pragma_*()` table functions and
`load_extension`. A compromised planner that reaches any of them reads
outside the annotated schema. Every shape below is refused by the parser or
denied absolutely by policy — never relabelled `restricted` and allowed.
"""

import pytest

from boundary.policy import Label, evaluate, query_label
from boundary.sql_ast import SUPPORTED_DIALECTS, SqlRejected, parse_template
from boundary.validator import validate_plan

LABELS = {
    "employees": {
        "label": "internal",
        "columns": {
            "department": "public",
            "role": "internal",
            "salary": "confidential",
            "full_name": "restricted",
            "iban": "restricted",
        },
    },
}


def _plan(template: str) -> dict:
    return {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "q",
        "steps": [
            {"id": "s1", "type": "query", "template": template, "params": {}},
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
        ],
    }


FILE_AND_CATALOG_READS = [
    ("duckdb", "SELECT * FROM '/etc/passwd'"),
    ("duckdb", "SELECT * FROM 'data.csv'"),
    ("duckdb", 'SELECT * FROM "secrets.parquet"'),
    ("duckdb", "SELECT * FROM 's3://bucket/dump.parquet'"),
    ("duckdb", "SELECT department FROM employees, 'other.csv' o"),
    ("duckdb", "SELECT * FROM read_text('/etc/passwd')"),
    ("duckdb", "SELECT * FROM read_json_auto('/home/user/.aws/credentials')"),
    ("duckdb", "SELECT * FROM glob('/home/*')"),
    ("duckdb", "SELECT * FROM duckdb_settings()"),
    ("duckdb", "SELECT * FROM duckdb_tables()"),
    ("duckdb", "SELECT * FROM sqlite_scan('other.db', 't')"),
    ("duckdb", "SELECT * FROM pg_tables"),
    ("duckdb", "SELECT * FROM sqlite_master"),
    ("duckdb", "SELECT department FROM other_db.employees"),
    ("duckdb", "SELECT department FROM employees, read_csv('x.csv') r"),
    ("sqlite", "SELECT * FROM sqlite_master"),
    ("sqlite", "SELECT * FROM sqlite_schema"),
    ("sqlite", "SELECT name FROM pragma_table_info('employees')"),
    ("sqlite", "SELECT department FROM main.employees"),
    ("sqlite", "SELECT department FROM temp.employees"),
]


@pytest.mark.parametrize(("dialect", "template"), FILE_AND_CATALOG_READS)
def test_file_and_catalog_reads_are_denied_absolutely(dialect, template):
    decision = evaluate(_plan(template), LABELS, "restricted", dialect=dialect)
    assert decision.allow is False
    assert decision.reason_code == "unknown_relation"
    assert query_label(template, LABELS, dialect=dialect) == Label.RESTRICTED


@pytest.mark.parametrize(
    "template",
    [
        "SELECT * FROM '/etc/passwd'",
        'SELECT * FROM "data.csv"',
        "SELECT * FROM 'https://evil.example/x.csv'",
        "SELECT * FROM 'C:\\Users\\x\\secrets.csv'",
    ],
)
def test_path_shaped_relations_are_refused_by_the_validator(template):
    # Schema-independent: a path can never be an annotated table, so the plan
    # is refused before policy runs, in every dialect.
    for dialect in SUPPORTED_DIALECTS:
        try:
            result = validate_plan(_plan(template), dialect=dialect)
        except Exception as exc:  # pragma: no cover - validator must never raise
            pytest.fail(f"validator raised {type(exc).__name__} for {dialect}")
        assert result.valid is False, (dialect, template)


@pytest.mark.parametrize(
    ("dialect", "template", "code"),
    [
        ("sqlite", "ATTACH DATABASE 'x.db' AS x", "statement_not_select"),
        ("sqlite", "PRAGMA table_info(employees)", "statement_not_select"),
        ("sqlite", "SELECT load_extension('evil') AS x FROM employees", "forbidden_function"),
        ("sqlite", "SELECT readfile('/etc/passwd') AS x FROM employees", "forbidden_function"),
        ("sqlite", "SELECT department FROM employees; DROP TABLE employees", "multi_statement"),
        ("sqlite", "SELECT department FROM employees -- c", "comment_forbidden"),
        ("duckdb", "SELECT getenv('HOME') AS h FROM employees", "forbidden_function"),
        ("duckdb", "SELECT current_setting('threads') AS t FROM employees", "forbidden_function"),
        ("duckdb", "SELECT COLUMNS('sal.*') FROM employees", "forbidden_function"),
        ("duckdb", "INSTALL httpfs", "statement_not_select"),
        ("duckdb", "LOAD httpfs", "statement_not_select"),
        ("duckdb", "SET enable_external_access = true", "statement_not_select"),
        ("duckdb", "COPY employees TO '/tmp/x.csv'", "statement_not_select"),
        ("duckdb", "EXPORT DATABASE '/tmp/x'", "sql_parse_error"),
        ("duckdb", "PIVOT employees ON department USING sum(salary)", "statement_not_select"),
    ],
)
def test_embedded_engine_statements_are_refused(dialect, template, code):
    with pytest.raises(SqlRejected) as info:
        parse_template(template, dialect)
    assert info.value.code == code


@pytest.mark.parametrize(
    ("template", "label"),
    [
        ("SELECT department FROM employees", Label.INTERNAL),  # table floor
        ("SELECT department, role FROM employees", Label.INTERNAL),
        ("SELECT department FROM employees WHERE salary > 100000", Label.CONFIDENTIAL),
        ("SELECT department FROM employees WHERE full_name GLOB 'J*'", Label.RESTRICTED),
        ("SELECT department FROM employees ORDER BY iban", Label.RESTRICTED),
    ],
)
def test_sqlite_dialect_is_column_precise(template, label):
    assert query_label(template, LABELS, dialect="sqlite") == label


def test_sqlite_dialect_is_selectable_by_env(monkeypatch):
    monkeypatch.setenv("FONDACO_SQL_DIALECT", "sqlite")
    assert validate_plan(_plan("SELECT department FROM employees")).valid is True
    assert query_label("SELECT department FROM employees", LABELS) == Label.INTERNAL
