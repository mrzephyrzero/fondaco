# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""`fondaco demo` — the whole boundary on synthetic data, in one command.

    fondaco demo [--host 127.0.0.1] [--port 8000] [--data-dir DIR]
                 [--planner auto|demo|llm] [--auto-approve]

What it does:
  1. generates a small, deterministic, entirely synthetic company dataset
     (`employees`, `orders`) into a DuckDB file, plus the reviewed labels file
     (`schema.yaml`) the DuckDB adapter reads;
  2. configures the boundary for it — DuckDB adapter opened read-only with
     external access off, duckdb dialect, egress clearance `internal`, an
     HMAC audit log in the data directory under a fresh random key;
  3. starts the web UI with the keyless fixture planner and its scripted
     questions and attacks — or a real planner, when one is configured.

Nothing here is a shortcut: the demo runs the production validator, policy
engine, guards, executor and audit log; only the data and the planner's
fixtures are canned. No real person's data is involved: names are drawn from
generic lists, IBANs are synthetic (valid check digits, fictitious bank
codes) and SSNs use the 9xx area the US SSA never issues.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import secrets
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

DB_NAME = "demo.duckdb"
SCHEMA_NAME = "schema.yaml"

SCHEMA_YAML = """\
# Labels for the `fondaco demo` dataset, in the format `fondaco discover`
# writes and the DuckDB adapter reads. Anything not listed is restricted.
format: fondaco-annotations
version: 1
tables:
  - name: employees
    label: internal
    comment: "One row per employee (synthetic)."
    columns:
      - {name: id, label: internal}
      - {name: full_name, label: restricted}
      - name: department
        label: public
        enum_values: [Engineering, Executive, Finance, HR, Operations, Sales]
      - name: role
        label: internal
        enum_values: [Analyst, CEO, CFO, Engineer, Intern, Manager, Specialist]
      - {name: salary, label: confidential, comment: "Gross yearly salary, EUR."}
      - {name: iban, label: restricted}
      - {name: ssn, label: restricted}
      - {name: hire_date, label: internal}
  - name: orders
    label: public
    comment: "Customer orders, one row each (synthetic)."
    columns:
      - {name: id, label: public}
      - {name: employee_id, label: internal, comment: "Sales rep, employees.id."}
      - name: region
        label: public
        enum_values: [Central, East, North, South, West]
      - name: status
        label: public
        enum_values: [CANCELLED, CONFIRMED, DELIVERED, PENDING, SHIPPED]
        business_description: "PENDING -> CONFIRMED -> SHIPPED -> DELIVERED, or CANCELLED."
      - {name: amount, label: internal, comment: "Order value, EUR."}
      - {name: order_date, label: public}
"""

_FIRST = (
    "Alex", "Bianca", "Carlo", "Dana", "Elena", "Femi", "Giulia", "Hiro", "Ines", "Jonas",
    "Kira", "Luca", "Maya", "Nico", "Olga", "Paolo", "Rosa", "Sami", "Tomas", "Vera",
)  # fmt: skip
_LAST = (
    "Rossi", "Bauer", "Novak", "Silva", "Moreau", "Kowalski", "Jensen", "Costa", "Weber",
    "Russo", "Dubois", "Lindqvist", "Horvat", "Marino", "Fischer", "Okafor",
)  # fmt: skip
_DEPARTMENTS = {  # department: (headcount, roles, salary range)
    "Engineering": (70, ("Engineer", "Manager", "Intern"), (42_000, 95_000)),
    "Sales": (60, ("Specialist", "Manager", "Intern"), (32_000, 80_000)),
    "Operations": (45, ("Specialist", "Analyst", "Manager"), (30_000, 70_000)),
    "Finance": (20, ("Analyst", "Manager"), (38_000, 85_000)),
    "HR": (8, ("Specialist", "Manager"), (34_000, 70_000)),
}
_REGIONS = ("North", "South", "East", "West", "Central")
_STATUSES = (
    ("DELIVERED", 50),
    ("SHIPPED", 15),
    ("CONFIRMED", 15),
    ("PENDING", 12),
    ("CANCELLED", 8),
)


def _iban(rng: random.Random) -> str:
    """A synthetic Italian IBAN with correct mod-97 check digits (fictitious ABI 99xxx)."""
    cin = chr(ord("A") + rng.randrange(26))
    bban = (
        f"{cin}99{rng.randrange(1000):03d}{rng.randrange(100000):05d}{rng.randrange(10**12):012d}"
    )
    numeric = "".join(str(int(ch, 36)) for ch in bban + "IT00")
    check = 98 - int(numeric) % 97
    return f"IT{check:02d}{bban}"


def _employees(rng: random.Random) -> list[tuple]:
    rows: list[tuple] = []
    start = date(2012, 1, 1)

    def add(name: str, department: str, role: str, salary: int) -> None:
        hired = start + timedelta(days=rng.randrange(5000))
        ssn = f"9{rng.randrange(100):02d}-{rng.randrange(1, 100):02d}-{rng.randrange(1, 10000):04d}"
        rows.append((len(rows) + 1, name, department, role, salary, _iban(rng), ssn, hired))

    # The executive team is deliberately tiny: two people. Any aggregate over
    # it is exactly the small group the k-threshold exists to suppress.
    add("Morgan Hale", "Executive", "CEO", 310_000)
    add("Rene Vidal", "Executive", "CFO", 245_000)
    add("John Doe", "Engineering", "Manager", 98_500)  # the binary-search target
    for department, (count, roles, (low, high)) in _DEPARTMENTS.items():
        for _ in range(count):
            name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
            role = roles[0] if rng.random() < 0.7 else rng.choice(roles)
            add(name, department, role, rng.randrange(low, high, 500))
    return rows


def _orders(rng: random.Random, sales_ids: list[int]) -> list[tuple]:
    statuses = [s for s, _ in _STATUSES]
    weights = [w for _, w in _STATUSES]
    start = date(2025, 1, 1)
    rows = []
    for i in range(1, 6001):
        rows.append(
            (
                i,
                rng.choice(sales_ids),
                rng.choice(_REGIONS),
                rng.choices(statuses, weights)[0],
                round(rng.uniform(40, 4_000), 2),
                start + timedelta(days=rng.randrange(683)),  # through 2026-11-14
            )
        )
    return rows


def build_demo_dataset(data_dir: str | Path) -> tuple[Path, Path]:
    """Write `demo.duckdb` and `schema.yaml` into `data_dir`; reuse them if present."""
    import duckdb

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path, schema_path = data_dir / DB_NAME, data_dir / SCHEMA_NAME
    schema_path.write_text(SCHEMA_YAML, encoding="utf-8")
    if db_path.exists():
        return db_path, schema_path

    rng = random.Random(20261115)  # noqa: S311 — synthetic data, not security
    employees = _employees(rng)
    sales = [r[0] for r in employees if r[2] == "Sales"]
    orders = _orders(rng, sales)
    tmp = db_path.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = duckdb.connect(str(tmp))
    try:
        with tempfile.TemporaryDirectory(prefix="fondaco-demo-build-") as work:
            for name, ddl, rows in (
                (
                    "employees",
                    "id INTEGER PRIMARY KEY, full_name VARCHAR, department VARCHAR, "
                    "role VARCHAR, salary INTEGER, iban VARCHAR, ssn VARCHAR, hire_date DATE",
                    employees,
                ),
                (
                    "orders",
                    "id INTEGER PRIMARY KEY, employee_id INTEGER, region VARCHAR, "
                    "status VARCHAR, amount DECIMAL(10, 2), order_date DATE",
                    orders,
                ),
            ):
                staged = Path(work) / f"{name}.csv"
                with staged.open("w", newline="", encoding="utf-8") as fh:
                    csv.writer(fh).writerows(rows)
                conn.execute(f"CREATE TABLE {name} ({ddl})")
                # Fixed table names and DDL; the staged path is a bound parameter.
                insert = f"INSERT INTO {name} SELECT * FROM read_csv(?, header = false)"  # noqa: S608
                conn.execute(insert, [str(staged)])
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
    tmp.rename(db_path)
    return db_path, schema_path


# ── Planner selection ─────────────────────────────────────────────────────

_PROVIDERS = {
    # env var with the key: (base URL, default model)
    "ANTHROPIC_API_KEY": ("https://api.anthropic.com/v1", "claude-sonnet-5"),
    "OPENAI_API_KEY": ("https://api.openai.com/v1", "gpt-4.1-mini"),
}
_OLLAMA = ("http://localhost:11434/v1", "qwen2.5-coder:7b")


def planner_env(choice: str, env: dict) -> dict[str, str]:
    """Environment for the planner: the keyless fixtures unless an LLM is configured.

    `auto`: an explicit FONDACO_PLANNER wins; else FONDACO_LLM_API_KEY, then
    ANTHROPIC_API_KEY, then OPENAI_API_KEY select a real planner; else fixtures.
    `llm` with no key anywhere means a local Ollama.
    """
    if choice == "demo":
        return {"FONDACO_PLANNER": "demo"}
    if choice == "auto" and (env.get("FONDACO_PLANNER") or "").strip():
        return {}
    if (env.get("FONDACO_LLM_API_KEY") or "").strip():
        return {"FONDACO_PLANNER": "llm"}
    for var, (base_url, model) in _PROVIDERS.items():
        key = (env.get(var) or "").strip()
        if key:
            return {
                "FONDACO_PLANNER": "llm",
                "FONDACO_LLM_API_KEY": key,
                "FONDACO_LLM_BASE_URL": env.get("FONDACO_LLM_BASE_URL") or base_url,
                "FONDACO_LLM_MODEL": env.get("FONDACO_LLM_MODEL") or model,
            }
    if choice == "llm":
        return {
            "FONDACO_PLANNER": "llm",
            "FONDACO_LLM_BASE_URL": env.get("FONDACO_LLM_BASE_URL") or _OLLAMA[0],
            "FONDACO_LLM_MODEL": env.get("FONDACO_LLM_MODEL") or _OLLAMA[1],
            "FONDACO_LLM_TEMPERATURE": env.get("FONDACO_LLM_TEMPERATURE") or "0",
        }
    return {"FONDACO_PLANNER": "demo"}


def demo_env(
    data_dir: Path, planner: str = "auto", auto_approve: bool = False, env: dict | None = None
) -> dict[str, str]:
    """Everything `fondaco demo` sets. Values already in `env` are kept where sensible."""
    env = dict(os.environ if env is None else env)
    db_path, schema_path = data_dir / DB_NAME, data_dir / SCHEMA_NAME
    out = {
        "FONDACO_ADAPTER": "duckdb",
        "FONDACO_SQL_DIALECT": "duckdb",
        "FONDACO_DUCKDB_PATH": str(db_path),
        "FONDACO_DUCKDB_SCHEMA": str(schema_path),
        "FONDACO_DEMO_FIXTURES": "local",
        "FONDACO_EGRESS_CLEARANCE": env.get("FONDACO_EGRESS_CLEARANCE") or "internal",
        "FONDACO_AUDIT_BACKEND": "file",
        "FONDACO_AUDIT_LOG": str(data_dir / "audit.jsonl"),
    }
    if not (env.get("FONDACO_AUDIT_HMAC_KEY") or "").strip():
        # A fresh key per data directory: the log in it verifies across
        # restarts, and nothing published can forge it.
        key_file = data_dir / "audit-hmac.key"
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(48), encoding="utf-8")
            key_file.chmod(0o600)
        out["FONDACO_AUDIT_HMAC_KEY"] = key_file.read_text(encoding="utf-8").strip()
        out["FONDACO_AUDIT_HMAC_KEY_ID"] = "demo"
    if auto_approve:
        out["FONDACO_AUTO_APPROVE_ENABLED"] = "true"
    for name in ("FONDACO_SCHEMA_ANNOTATIONS", "DATABASE_URL"):
        out[name] = ""  # the demo's own labels file is the only overlay
    out.update(planner_env(planner, env))
    return out


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fondaco demo", description="Zero-config Fondaco demo on synthetic DuckDB data."
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default loopback)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="where the dataset, labels and audit log live (default: a new temp dir)",
    )
    parser.add_argument(
        "--planner",
        choices=("auto", "demo", "llm"),
        default="auto",
        help="auto: fixtures unless an LLM key is set (FONDACO_LLM_API_KEY, "
        "ANTHROPIC_API_KEY, OPENAI_API_KEY); llm with no key: local Ollama",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="let low-risk plans (label <= internal) run without a human click",
    )
    parser.add_argument("--build-only", action="store_true", help="write the dataset and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        import duckdb  # noqa: F401
    except ImportError:
        print("fondaco demo needs DuckDB: pip install 'fondaco[duckdb]'", file=sys.stderr)
        return 1
    data_dir = args.data_dir or Path(tempfile.mkdtemp(prefix="fondaco-demo-"))
    db_path, schema_path = build_demo_dataset(data_dir)
    if args.build_only:
        print(f"dataset: {db_path}\nlabels:  {schema_path}")
        return 0

    os.environ.update(demo_env(data_dir, args.planner, args.auto_approve))
    planner = os.environ.get("FONDACO_PLANNER", "demo")
    url_host = "localhost" if args.host in ("0.0.0.0", "127.0.0.1", "::") else args.host  # noqa: S104
    print(
        "\n  Fondaco demo — data stays home, plans cross.\n"
        f"  UI:        http://{url_host}:{args.port}\n"
        f"  data:      {data_dir}  (synthetic, DuckDB, opened read-only)\n"
        f"  labels:    {schema_path}\n"
        f"  planner:   {'fixtures (no LLM, no key)' if planner == 'demo' else 'LLM'}\n"
        f"  clearance: {os.environ['FONDACO_EGRESS_CLEARANCE']}\n"
        f"  MCP:       fondaco mcp --api-url http://{url_host}:{args.port}\n",
        flush=True,
    )
    import uvicorn

    # api.main builds the app from the environment at import, so import it
    # only now that the environment is set.
    uvicorn.run("api.main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
