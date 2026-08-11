# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Adapter conformance against the live demo database."""

import pytest

from tests.integration.conftest import requires_db

pytestmark = requires_db


def _step(template, params=None):
    return {"id": "s1", "type": "query", "template": template, "params": params or {}}


def test_schema_labels_and_fallback(adapter):
    schema = adapter.get_schema()
    tables = {t.name: t for t in schema.tables}
    assert tables["orders"].label == "internal"
    assert tables["products"].label == "public"
    assert tables["customers"].label == "restricted"
    columns = {c.name: c for c in tables["customers"].columns}
    # customers.phone has no label annotation → must fall back to restricted.
    assert columns["phone"].label == "restricted"
    assert columns["city"].label == "confidential"
    assert tables["orders"].row_count > 0  # coarse statistic, never row data


@requires_db
def test_column_label_raises_its_table_against_real_schema(adapter):
    # employees is annotated 'internal' but carries a 'restricted' salary column.
    # The whole table therefore labels restricted, even for a template that never
    # mentions salary — the over-approximation of label-model.md §4, read off a
    # real Postgres schema rather than a hand-built fixture.
    from boundary.policy import Label, query_label
    from executor.adapters.contract import schema_labels_dict

    schema = adapter.get_schema()
    tables = {t.name: t for t in schema.tables}
    assert tables["employees"].label == "internal"
    columns = {c.name: c for c in tables["employees"].columns}
    assert columns["salary"].label == "restricted"
    assert columns["department"].label == "internal"

    labels = schema_labels_dict(schema)
    assert query_label("SELECT department FROM employees", labels) == Label.RESTRICTED


@requires_db
def test_relabeling_at_the_source_takes_effect_without_restart(adapter):
    # label-model.md §4 makes relabeling at the source the only way to change
    # classification. That only holds if the adapter re-reads the labels: it
    # used to cache them for the life of the process, so a column raised to
    # restricted kept crossing at its old label until someone restarted the app.
    # Labels are now read on the same connection as the query itself.
    import psycopg

    from tests.integration.conftest import ADMIN_DSN

    before = adapter.execute(_step("SELECT category FROM products"))
    assert before.label == "public"

    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute("COMMENT ON COLUMN products.category IS 'label:restricted temporarily'")
    try:
        after = adapter.execute(_step("SELECT category FROM products"))
        assert after.label == "restricted", "the adapter is serving stale labels"
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute("COMMENT ON COLUMN products.category IS 'label:public'")

    assert adapter.execute(_step("SELECT category FROM products")).label == "public"


def test_execute_binds_params_and_labels(adapter):
    result = adapter.execute(
        _step(
            "SELECT region, status FROM orders WHERE order_date >= %(since)s",
            {"since": {"type": "date", "value": "2025-06-01"}},
        )
    )
    assert result.columns == ("region", "status")
    assert 0 < result.row_count <= 10_000
    assert result.label == "internal"


def test_row_overflow_errors_rather_than_truncates(seeded_database):
    from executor.adapters.contract import AdapterError
    from executor.adapters.postgres import PostgresAdapter

    small = PostgresAdapter(seeded_database, max_rows=100)
    with pytest.raises(AdapterError) as excinfo:
        small.execute(_step("SELECT id FROM orders"))
    assert excinfo.value.kind == "limit_exceeded"


def test_errors_are_sanitized(adapter):
    from executor.adapters.contract import AdapterError

    secret = "customer-secret-value"  # noqa: S105 — canary param value, not a credential
    with pytest.raises(AdapterError) as excinfo:
        adapter.execute(
            _step(
                "SELECT missing_column FROM orders WHERE region = %(r)s",
                {"r": {"type": "string", "value": secret}},
            )
        )
    assert excinfo.value.kind == "execution"
    assert secret not in str(excinfo.value)
    assert "missing_column" not in str(excinfo.value)  # driver text never passes through


def test_only_query_steps_accepted(adapter):
    from executor.adapters.contract import AdapterError

    with pytest.raises(AdapterError):
        adapter.execute({"id": "s1", "type": "aggregate", "input": "s0"})
