# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""`fondaco discover` — local schema classification for onboarding a source.

    fondaco discover --dsn postgresql://... [--schema public]
    fondaco discover --csv orders.csv [--table orders]
    fondaco discover --excel export.xlsx
        [--format json|yaml] [--out annotations.json]
        [--sql-comments comments.sql] [--max-rows 500]

Zero LLM calls, zero network beyond the scanned database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from executor.discovery.classifier import MAX_SAMPLE_ROWS
from executor.discovery.output import to_json, to_sql_comments, to_yaml
from executor.discovery.sources import (
    DiscoveryError,
    discover_csv,
    discover_excel,
    discover_postgres,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fondaco")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("discover", help="classify a source locally and propose annotations")
    src = d.add_mutually_exclusive_group(required=True)
    src.add_argument("--dsn", help="Postgres connection string (read-only access is enough)")
    src.add_argument("--csv", type=Path, help="CSV file to sample")
    src.add_argument("--excel", type=Path, help="Excel workbook (.xlsx); one table per sheet")
    d.add_argument("--schema", default="public", help="Postgres schema (default: public)")
    d.add_argument("--table", help="table name for a CSV source (default: file stem)")
    d.add_argument("--format", choices=("json", "yaml"), default="json")
    d.add_argument("--out", type=Path, help="write the proposal here (default: stdout)")
    d.add_argument("--sql-comments", type=Path, help="also write COMMENT ON SQL for Postgres")
    d.add_argument("--max-rows", type=int, default=MAX_SAMPLE_ROWS, help="≤ 500")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_rows > MAX_SAMPLE_ROWS or args.max_rows < 1:
        print(f"--max-rows must be between 1 and {MAX_SAMPLE_ROWS}", file=sys.stderr)
        return 2
    try:
        if args.dsn:
            findings = discover_postgres(args.dsn, schema=args.schema, max_rows=args.max_rows)
        elif args.csv:
            findings = discover_csv(args.csv, table=args.table, max_rows=args.max_rows)
        else:
            findings = discover_excel(args.excel, max_rows=args.max_rows)
    except DiscoveryError as exc:
        print(f"discovery failed: {exc}", file=sys.stderr)
        return 1

    rendered = to_yaml(findings) if args.format == "yaml" else to_json(findings)
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    if args.sql_comments:
        schema = args.schema if args.dsn else "public"
        args.sql_comments.write_text(to_sql_comments(findings, schema), encoding="utf-8")
    return 0
