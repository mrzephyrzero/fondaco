# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Where discovery samples from: Postgres, DuckDB, a CSV file, an Excel workbook.

Every source reads at most MAX_SAMPLE_ROWS rows per table into memory and
hands them straight to the classifier; nothing is written anywhere. The
Postgres source connects read-only with a statement timeout and builds every
identifier with psycopg.sql — never by string formatting.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from executor.discovery.classifier import MAX_SAMPLE_ROWS, TableFinding, classify_table

_PG_TABLES_SQL = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod)
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""


class DiscoveryError(Exception):
    """A source could not be read. The message never contains sampled values."""


def _cap(max_rows: int) -> int:
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        return MAX_SAMPLE_ROWS
    return min(max_rows, MAX_SAMPLE_ROWS)


def discover_postgres(
    dsn: str, schema: str = "public", max_rows: int = MAX_SAMPLE_ROWS, timeout_s: int = 30
) -> list[TableFinding]:
    import psycopg
    from psycopg import sql

    limit = _cap(max_rows)
    try:
        with psycopg.connect(
            dsn,
            connect_timeout=10,
            options=(
                f"-c default_transaction_read_only=on -c statement_timeout={int(timeout_s) * 1000}"
            ),
        ) as conn:
            tables: dict[str, list[tuple[str, str]]] = {}
            for table, column, sql_type in conn.execute(_PG_TABLES_SQL, (schema,)).fetchall():
                tables.setdefault(table, []).append((column, sql_type))
            findings = []
            for table, columns in tables.items():
                query = sql.SQL("SELECT {cols} FROM {schema}.{table} LIMIT {limit}").format(
                    cols=sql.SQL(", ").join(sql.Identifier(c) for c, _ in columns),
                    schema=sql.Identifier(schema),
                    table=sql.Identifier(table),
                    limit=sql.Literal(limit),
                )
                rows = conn.execute(query).fetchall()
                findings.append(classify_table(table, columns, rows, source=f"postgres:{schema}"))
                del rows  # the sample lives only as long as its classification
            return findings
    except psycopg.Error as exc:
        raise DiscoveryError(f"postgres source failed: {type(exc).__name__}") from exc


def discover_duckdb(
    path: str | Path, max_rows: int = MAX_SAMPLE_ROWS, timeout_s: int = 30
) -> list[TableFinding]:
    """Sample every base table of a DuckDB file, opened with the adapter's lockdown."""
    import threading

    try:
        import duckdb
    except ImportError as exc:
        raise DiscoveryError("DuckDB source needs: pip install 'fondaco[duckdb]'") from exc
    from executor.adapters.duckdb_adapter import LOCKED_CONFIG, SCHEMA_SQL, quote_ident

    path = Path(path)
    if not path.is_file():
        raise DiscoveryError("duckdb source: file not found")
    limit = _cap(max_rows)
    try:
        conn = duckdb.connect(str(path), read_only=True, config=dict(LOCKED_CONFIG))
    except duckdb.Error as exc:
        raise DiscoveryError(f"duckdb source failed: {type(exc).__name__}") from exc
    timer = threading.Timer(float(timeout_s), conn.interrupt)
    timer.start()
    try:
        tables: dict[str, list[tuple[str, str]]] = {}
        for table, column, sql_type in conn.execute(SCHEMA_SQL).fetchall():
            tables.setdefault(table, []).append((column, str(sql_type)))
        findings = []
        for table, columns in tables.items():
            cols = ", ".join(quote_ident(c) for c, _ in columns)
            # Identifiers come from the catalog and are quoted; the limit is an int.
            rows = conn.execute(
                f"SELECT {cols} FROM {quote_ident(table)} "  # noqa: S608
                f"USING SAMPLE reservoir({int(limit)} ROWS) REPEATABLE (0)"
            ).fetchall()
            findings.append(classify_table(table, columns, rows, source=f"duckdb:{path.name}"))
            del rows  # the sample lives only as long as its classification
        return findings
    except duckdb.Error as exc:
        raise DiscoveryError(f"duckdb source failed: {type(exc).__name__}") from exc
    finally:
        timer.cancel()
        conn.close()


def _reservoir(rows, limit: int) -> list:
    """Uniform sample of at most `limit` rows from a stream; seeded, so reproducible."""
    rng = random.Random(0)  # noqa: S311 — sampling, not security
    sample: list = []
    for i, row in enumerate(rows):
        if i < limit:
            sample.append(row)
        else:
            j = rng.randint(0, i)
            if j < limit:
                sample[j] = row
    return sample


def discover_csv(
    path: str | Path, table: str | None = None, max_rows: int = MAX_SAMPLE_ROWS
) -> list[TableFinding]:
    path = Path(path)
    name = table or path.stem
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            sniff = fh.read(4096)
            fh.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sniff, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(fh, dialect)
            header = next(reader, None)
            if not header:
                raise DiscoveryError("csv source has no header row")
            rows = _reservoir((tuple(r) for r in reader), _cap(max_rows))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise DiscoveryError(f"csv source failed: {type(exc).__name__}") from exc
    columns = [(h.strip() or f"column_{i + 1}", "text") for i, h in enumerate(header)]
    return [classify_table(name, columns, rows, source=f"csv:{path.name}")]


def discover_excel(path: str | Path, max_rows: int = MAX_SAMPLE_ROWS) -> list[TableFinding]:
    try:
        import openpyxl
    except ImportError as exc:
        raise DiscoveryError(
            "Excel discovery needs the optional extra: pip install 'fondaco[excel]'"
        ) from exc
    path = Path(path)
    findings = []
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                rows_iter = sheet.iter_rows(values_only=True)
                header = next(rows_iter, None)
                if not header:
                    continue
                rows = _reservoir((tuple(r) for r in rows_iter), _cap(max_rows))
                columns = [
                    (str(h).strip() if h is not None else f"column_{i + 1}", "text")
                    for i, h in enumerate(header)
                ]
                findings.append(
                    classify_table(sheet.title, columns, rows, source=f"excel:{path.name}")
                )
        finally:
            workbook.close()
    except DiscoveryError:
        raise
    except Exception as exc:  # openpyxl raises a zoo of types on bad files
        raise DiscoveryError(f"excel source failed: {type(exc).__name__}") from exc
    return findings
