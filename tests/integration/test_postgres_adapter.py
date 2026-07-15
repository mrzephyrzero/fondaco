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
