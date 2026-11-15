# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Adapter selection: the boundary's dialect must match the database's grammar."""

import pytest

from executor.adapters.bigquery import BigQueryAdapter
from executor.adapters.factory import AdapterConfigError, adapter_from_env, check_dialect
from executor.adapters.postgres import PostgresAdapter
from executor.adapters.sqlserver import SqlServerAdapter


def test_default_is_postgres():
    assert isinstance(adapter_from_env(env={"DATABASE_URL": "postgresql://x"}), PostgresAdapter)


def test_sqlserver_and_bigquery_selected_with_matching_dialect():
    mssql = {
        "FONDACO_ADAPTER": "sqlserver",
        "FONDACO_SQL_DIALECT": "tsql",
        "FONDACO_MSSQL_CONNECTION_STRING": "Server=db;",
    }
    assert isinstance(adapter_from_env(env=mssql), SqlServerAdapter)
    bq = {
        "FONDACO_ADAPTER": "bigquery",
        "FONDACO_SQL_DIALECT": "bigquery",
        "FONDACO_BQ_PROJECT": "acme-analytics",
        "FONDACO_BQ_DATASET": "erp",
        "FONDACO_BQ_MAX_BYTES_BILLED": "1000",
    }
    assert isinstance(adapter_from_env(env=bq), BigQueryAdapter)


@pytest.mark.parametrize(
    ("kind", "dialect"),
    [("sqlserver", ""), ("sqlserver", "postgres"), ("bigquery", "tsql"), ("postgres", "duckdb")],
)
def test_dialect_mismatch_refuses_to_start(kind, dialect):
    with pytest.raises(AdapterConfigError):
        check_dialect(kind, {"FONDACO_SQL_DIALECT": dialect})


@pytest.mark.parametrize(
    "env",
    [
        {"FONDACO_ADAPTER": "oracle"},
        {"FONDACO_ADAPTER": "sqlserver", "FONDACO_SQL_DIALECT": "tsql"},  # no connection string
        {"FONDACO_ADAPTER": "bigquery", "FONDACO_SQL_DIALECT": "bigquery",
         "FONDACO_BQ_PROJECT": "acme-analytics", "FONDACO_BQ_DATASET": "erp",
         "FONDACO_BQ_MAX_BYTES_BILLED": "lots"},
    ],
)  # fmt: skip
def test_bad_configuration_refuses_to_start(env):
    with pytest.raises(AdapterConfigError):
        adapter_from_env(env=env)


def test_app_refuses_to_start_on_mismatch(monkeypatch, tmp_path):
    from api.main import create_app

    monkeypatch.setenv("FONDACO_ADAPTER", "sqlserver")
    monkeypatch.delenv("FONDACO_SQL_DIALECT", raising=False)
    with pytest.raises(AdapterConfigError):
        create_app(audit_path=str(tmp_path / "a.jsonl"))
