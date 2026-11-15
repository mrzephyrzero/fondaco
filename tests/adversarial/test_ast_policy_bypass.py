# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Label-bypass attempts against the syntax-tree analyser.

The attacker controls the template (compromised planner) and wants a
restricted column to leave at a lower label. Each test is one bypass shape:
hiding the column behind a CTE, a derived table, a comma-join, a correlated
or nested subquery, a set operation, a name that shadows a table, a filter
instead of a projection, or a relation the schema does not contain. Every one
must come out restricted or be denied outright.
"""

import pytest

from boundary.policy import Label, analyze_query, evaluate, query_label
from boundary.sql_ast import SqlRejected, parse_template
from boundary.validator import validate_plan

LABELS = {
    "orders": {
        "label": "internal",
        "columns": {
            "id": "internal",
            "customer_id": "internal",
            "region": "internal",
            "status": "internal",
        },
    },
    "customers": {
        "label": "restricted",
        "columns": {"id": "internal", "email": "restricted", "city": "confidential"},
    },
    # An internal table with one restricted column: the column-level case.
    "employees": {
        "label": "internal",
        "columns": {"id": "internal", "department": "internal", "salary": "restricted"},
    },
}


def _plan(template: str, params: dict | None = None) -> dict:
    return {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "q",
        "steps": [
            {"id": "s1", "type": "query", "template": template, "params": params or {}},
            {"id": "s2", "type": "present", "input": "s1", "format": "table", "title": "t"},
        ],
    }


# ── Bypass via CTE / subquery / comma-join ───────────────────────────────

CTE_AND_SUBQUERY_BYPASSES = [
    # CTE that renames the restricted column
    "WITH x AS (SELECT email AS region FROM customers) SELECT region FROM x",
    # CTE that shadows a real, lower-labelled table name
    "WITH orders AS (SELECT email AS region FROM customers) SELECT region FROM orders",
    # nested CTEs
    "WITH a AS (SELECT email FROM customers), b AS (SELECT email AS e FROM a) SELECT e FROM b",
    # derived table with renaming
    "SELECT t.r FROM (SELECT email AS r FROM customers) t",
    # derived table two levels deep
    "SELECT u.r FROM (SELECT t.r FROM (SELECT email AS r FROM customers) t) u",
    # scalar subquery in the select list
    "SELECT (SELECT max(email) FROM customers) AS r FROM orders",
    # correlated subquery in WHERE
    "SELECT region FROM orders o WHERE EXISTS "
    "(SELECT 1 FROM customers c WHERE c.id = o.customer_id AND c.email LIKE 'a%%')",
    # IN-subquery
    "SELECT region FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE city = 'X')",
    # comma-join, restricted column in projection (CRIT-1)
    "SELECT c.email FROM orders o, customers c WHERE o.customer_id = c.id",
    # comma-join, restricted column only in the predicate
    "SELECT o.region FROM orders o, customers c WHERE o.customer_id = c.id AND c.email = 'x'",
    # comma-join with three relations, the restricted one last
    "SELECT o.region FROM orders o, employees e, customers c WHERE c.email = e.department",
    # set operations
    "SELECT region FROM orders UNION SELECT email FROM customers",
    "SELECT region FROM orders EXCEPT SELECT email FROM customers",
    # restricted column in ORDER BY via a subquery
    "SELECT region FROM orders ORDER BY (SELECT max(salary) FROM employees)",
    # case-variant identifiers resolve to the same table (no longer unknown)
    "SELECT EMAIL FROM CUSTOMERS",
]


@pytest.mark.parametrize("template", CTE_AND_SUBQUERY_BYPASSES)
def test_bypass_lands_above_confidential(template):
    label = query_label(template, LABELS)
    assert label >= Label.CONFIDENTIAL
    assert evaluate(_plan(template), LABELS, "internal").allow is False


def test_bypass_through_cte_is_detected_not_guessed():
    # The CTE case is *resolved*: the analyser names customers.email as read.
    analysis = analyze_query("WITH x AS (SELECT email FROM customers) SELECT email FROM x", LABELS)
    assert analysis.resolved is True
    assert ("customers", "email") in analysis.columns
    assert analysis.label == Label.RESTRICTED


# ── Column-level granularity inside an internal table ────────────────────


def test_internal_columns_of_mixed_table_stay_internal():
    decision = evaluate(_plan("SELECT department FROM employees"), LABELS, "internal")
    assert decision.allow is True
    assert decision.plan_label == "internal"


@pytest.mark.parametrize(
    "template",
    [
        "SELECT department FROM employees WHERE salary > %(x)s",
        "SELECT department FROM employees WHERE salary BETWEEN %(x)s AND %(x)s",
        "SELECT department FROM employees ORDER BY salary DESC",
        "SELECT department FROM employees GROUP BY department HAVING avg(salary) > %(x)s",
        "SELECT department FROM employees WHERE CASE WHEN salary > %(x)s THEN 1 ELSE 0 END = 1",
        "SELECT a.department FROM employees a JOIN employees b ON a.salary > b.salary",
        "SELECT department, count(*) AS n FROM employees WHERE salary IS NULL GROUP BY department",
    ],
)
def test_restricted_column_filter_inside_internal_table(template):
    # Binary search on salary through a WHERE clause is the textbook inference
    # attack; the filter column is read, so the result is restricted.
    params = {"x": {"type": "int", "value": 50000}} if "%(x)s" in template else {}
    decision = evaluate(_plan(template, params), LABELS, "internal")
    assert decision.allow is False
    assert decision.reason_code == "label_exceeds_clearance"
    assert decision.plan_label == "restricted"


def test_star_expands_to_every_column():
    # `*` is expanded against the schema, so it reads salary too.
    assert query_label("SELECT * FROM employees", LABELS) == Label.RESTRICTED


def test_whole_row_reference_fails_closed():
    for t in [
        "SELECT e FROM employees e",
        "SELECT count(e) AS n FROM employees e",
        "SELECT (e).salary FROM employees e",
    ]:
        assert query_label(t, LABELS) == Label.RESTRICTED, t


# ── Relations outside the annotated schema: absolute deny ────────────────

UNKNOWN_RELATIONS = [
    ("duckdb", "SELECT * FROM read_csv('/etc/passwd')"),
    ("duckdb", "SELECT * FROM read_csv_auto('/data/secrets.csv')"),
    ("duckdb", "SELECT * FROM read_parquet('s3://bucket/x.parquet')"),
    ("postgres", "SELECT usename FROM pg_catalog.pg_user"),
    ("postgres", "SELECT table_name FROM information_schema.tables"),
    ("postgres", "SELECT column_name FROM information_schema.columns"),
    ("postgres", "SELECT email FROM public.customers"),
    ("postgres", "SELECT relname FROM pg_class"),
    ("postgres", "SELECT email FROM customer_emails()"),
    ("postgres", "SELECT n FROM generate_series(1, 10) n"),
    ("postgres", "SELECT x FROM (VALUES (1), (2)) v(x)"),
    ("postgres", "SELECT region FROM orders o CROSS JOIN LATERAL generate_series(1, 2) g"),
    ("tsql", "SELECT name FROM sys.tables"),
    ("tsql", "SELECT name FROM master.dbo.sysdatabases"),
    ("bigquery", "SELECT * FROM `project.dataset.customers`"),
    ("snowflake", "SELECT * FROM snowflake.account_usage.query_history"),
    ("postgres", "SELECT region FROM orders WHERE customer_id IN (SELECT 1 FROM pg_stat_activity)"),
]


@pytest.mark.parametrize(("dialect", "template"), UNKNOWN_RELATIONS)
def test_unknown_relation_is_denied_even_at_restricted_clearance(dialect, template):
    decision = evaluate(_plan(template), LABELS, "restricted", dialect=dialect)
    assert decision.allow is False
    assert decision.reason_code == "unknown_relation"


@pytest.mark.parametrize(("dialect", "template"), UNKNOWN_RELATIONS)
def test_unknown_relation_never_labels_below_restricted(dialect, template):
    # The executor-side helper cannot deny; it must still never lower a label.
    assert query_label(template, LABELS, dialect=dialect) == Label.RESTRICTED


@pytest.mark.parametrize(
    "template",
    [
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT usename FROM pg_catalog.pg_user",
        "SELECT email FROM customer_emails()",
    ],
)
def test_validator_rejects_schema_independent_foreign_relations(template):
    # Functions in FROM and qualified names can never be in the annotated
    # schema, so the validator refuses them before policy runs at all.
    result = validate_plan(_plan(template), dialect="duckdb")
    assert result.valid is False
    assert "unknown_relation" in {e.code for e in result.errors}


# ── Hardening: statements, constructs, functions ─────────────────────────


@pytest.mark.parametrize(
    ("dialect", "template", "code"),
    [
        ("postgres", "SELECT 1; SELECT 2", "multi_statement"),
        ("postgres", "SELECT region INTO evil FROM orders", "forbidden_construct"),
        ("postgres", "SELECT region FROM orders FOR UPDATE", "forbidden_construct"),
        (
            "postgres",
            "WITH x AS (DELETE FROM orders RETURNING id) SELECT id FROM x",
            "forbidden_construct",
        ),
        (
            "postgres",
            "WITH x AS (UPDATE orders SET region = 'x' RETURNING id) SELECT id FROM x",
            "forbidden_construct",
        ),
        ("postgres", "INSERT INTO orders (id) VALUES (1)", "statement_not_select"),
        ("postgres", "UPDATE orders SET region = 'x'", "statement_not_select"),
        ("postgres", "DELETE FROM orders", "statement_not_select"),
        ("postgres", "DROP TABLE orders", "statement_not_select"),
        ("postgres", "ALTER TABLE orders ADD COLUMN x int", "statement_not_select"),
        ("postgres", "COPY orders TO '/tmp/x'", "statement_not_select"),
        ("postgres", "CALL do_something()", "statement_not_select"),
        ("postgres", "SET search_path = evil", "statement_not_select"),
        ("duckdb", "ATTACH 'evil.db'", "statement_not_select"),
        ("duckdb", "PRAGMA database_list", "statement_not_select"),
        ("tsql", "EXEC xp_cmdshell 'dir'", "statement_not_select"),
        ("postgres", "SELECT region /* c */ FROM orders", "comment_forbidden"),
        ("postgres", "SELECT region FROM orders -- c", "comment_forbidden"),
        ("postgres", "SELECT pg_read_file('/etc/passwd') AS x FROM orders", "forbidden_function"),
        ("postgres", "SELECT dblink('host=evil', 'x') AS x FROM orders", "forbidden_function"),
        ("postgres", "SELECT pg_sleep(10) AS x FROM orders", "forbidden_function"),
        ("postgres", "SELECT set_config('a', 'b', false) AS x FROM orders", "forbidden_function"),
        ("postgres", "SELECT row_number() OVER () AS n FROM orders", "forbidden_function"),
        ("postgres", "SELECT region FROM orders WHERE region ~ 'x'", "forbidden_function"),
        ("postgres", "SELECT region FROM orders WHERE id = ?", "invalid_placeholder"),
        ("postgres", "SELECT region FROM orders WHERE id = :id", "invalid_placeholder"),
        ("postgres", "SELECT region FROM orders WHERE id = $1", "forbidden_construct"),
        ("postgres", "SELEC region FROM orders", "sql_parse_error"),
    ],
)
def test_parser_refuses(dialect, template, code):
    with pytest.raises(SqlRejected) as info:
        parse_template(template, dialect)
    assert info.value.code == code


def test_unknown_dialect_is_refused_not_defaulted():
    with pytest.raises(SqlRejected) as info:
        parse_template("SELECT region FROM orders", "mysql")
    assert info.value.code == "unsupported_dialect"
    assert validate_plan(_plan("SELECT region FROM orders"), dialect="mysql").valid is False
    assert (
        evaluate(_plan("SELECT region FROM orders"), LABELS, "restricted", "mysql").allow is False
    )


def test_unknown_dialect_from_env_fails_closed(monkeypatch):
    monkeypatch.setenv("FONDACO_SQL_DIALECT", "oracle")
    assert validate_plan(_plan("SELECT region FROM orders")).valid is False
    assert query_label("SELECT region FROM orders", LABELS) == Label.RESTRICTED


@pytest.mark.parametrize("dialect", ["postgres", "duckdb", "tsql", "bigquery", "snowflake"])
def test_legit_query_resolves_in_every_supported_dialect(dialect):
    template = "SELECT region, status FROM orders WHERE customer_id > %(n)s"
    plan = _plan(template, {"n": {"type": "int", "value": 3}})
    assert validate_plan(plan, dialect=dialect).valid is True
    decision = evaluate(plan, _catalog_names(LABELS, dialect), "internal", dialect=dialect)
    assert decision.allow is True
    assert decision.plan_label == "internal"


def _catalog_names(labels: dict, dialect: str) -> dict:
    # Schema names are exact catalog names. Snowflake stores unquoted names
    # upper-case, so its adapter would report ORDERS, not orders.
    if dialect != "snowflake":
        return labels
    return {
        t.upper(): {
            "label": v["label"],
            "columns": {c.upper(): lab for c, lab in v["columns"].items()},
        }
        for t, v in labels.items()
    }


def test_identifier_case_follows_the_dialect_and_mismatch_fails_closed():
    # Lower-case catalog names under Snowflake's upper-casing rule: the
    # relation is not found, and that is a deny — never a fuzzy match.
    decision = evaluate(_plan("SELECT region FROM orders"), LABELS, "restricted", "snowflake")
    assert decision.allow is False
    assert decision.reason_code == "unknown_relation"


def test_placeholder_values_never_reach_the_parser():
    # The parser sees `?` in place of each %(name)s; a hostile value cannot
    # change the tree because it is never spliced into the template.
    tree = parse_template("SELECT region FROM orders WHERE region = %(r)s")
    assert "%(r)s" not in tree.sql()
