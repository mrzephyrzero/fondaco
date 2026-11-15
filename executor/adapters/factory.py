# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Pick the source adapter from the environment: FONDACO_ADAPTER.

    postgres  (default)  DATABASE_URL
    sqlserver            FONDACO_MSSQL_CONNECTION_STRING, FONDACO_MSSQL_SCHEMA (dbo)
    bigquery             FONDACO_BQ_PROJECT, FONDACO_BQ_DATASET,
                         FONDACO_BQ_MAX_BYTES_BILLED, FONDACO_BQ_LOCATION

Each adapter speaks one SQL grammar, and the boundary must parse plans in that
same grammar. `FONDACO_SQL_DIALECT` therefore has to name the adapter's
dialect: a mismatch, or a non-Postgres adapter with the dialect left unset,
stops the app at startup instead of letting the boundary reason about one
grammar while the database runs another.
"""

from __future__ import annotations

import os

from boundary.sql_ast import DEFAULT_DIALECT, DIALECT_ENV
from executor.adapters.annotations import Annotations

ADAPTER_ENV = "FONDACO_ADAPTER"
ADAPTER_DIALECTS = {"postgres": "postgres", "sqlserver": "tsql", "bigquery": "bigquery"}


class AdapterConfigError(RuntimeError):
    """The adapter configuration is unusable; the app must not start."""


def adapter_kind(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    kind = (env.get(ADAPTER_ENV) or "postgres").strip().lower()
    if kind not in ADAPTER_DIALECTS:
        raise AdapterConfigError(f"{ADAPTER_ENV} must be one of {sorted(ADAPTER_DIALECTS)}")
    return kind


def check_dialect(kind: str, env: dict | None = None) -> str:
    """The dialect the boundary must parse with for this adapter, or refuse."""
    env = os.environ if env is None else env
    expected = ADAPTER_DIALECTS[kind]
    configured = (env.get(DIALECT_ENV) or "").strip().lower()
    if not configured and expected != DEFAULT_DIALECT:
        raise AdapterConfigError(f"{ADAPTER_ENV}={kind} requires {DIALECT_ENV}={expected}")
    if configured and configured != expected:
        raise AdapterConfigError(
            f"{DIALECT_ENV}={configured} does not match {ADAPTER_ENV}={kind} ({expected})"
        )
    return expected


def _int(env: dict, name: str, default: int) -> int:
    try:
        value = int(env.get(name, default))
    except ValueError as exc:
        raise AdapterConfigError(f"{name} must be an integer") from exc
    if value < 1:
        raise AdapterConfigError(f"{name} must be positive")
    return value


def adapter_from_env(annotations: Annotations | None = None, env: dict | None = None):
    env = os.environ if env is None else env
    kind = adapter_kind(env)
    check_dialect(kind, env)
    try:
        if kind == "postgres":
            from executor.adapters.postgres import PostgresAdapter

            return PostgresAdapter(env["DATABASE_URL"], annotations=annotations)
        if kind == "sqlserver":
            from executor.adapters.sqlserver import SqlServerAdapter

            return SqlServerAdapter(
                env["FONDACO_MSSQL_CONNECTION_STRING"],
                schema=env.get("FONDACO_MSSQL_SCHEMA", "dbo"),
                annotations=annotations,
            )
        from executor.adapters.bigquery import DEFAULT_MAX_BYTES_BILLED, BigQueryAdapter

        return BigQueryAdapter(
            env["FONDACO_BQ_PROJECT"],
            env["FONDACO_BQ_DATASET"],
            maximum_bytes_billed=_int(env, "FONDACO_BQ_MAX_BYTES_BILLED", DEFAULT_MAX_BYTES_BILLED),
            location=env.get("FONDACO_BQ_LOCATION") or None,
            annotations=annotations,
        )
    except KeyError as exc:
        raise AdapterConfigError(f"{kind} adapter needs {exc.args[0]}") from exc
