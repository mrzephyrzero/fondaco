# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Deterministic demo planner — pre-generated fixture plans, no LLM.

Powers the keyless `docker compose up` demo. It is transparently a fixture:
it returns hand-written plans for the ten scripted questions and nothing
else. Crucially it is NOT a shortcut around the boundary — every fixture
plan is built and validated through `planner.client.assemble_plan`, the
exact same path the live LLM planner uses, and then flows through the same
policy engine, executor, guards, and audit log. The plans are fixtures;
the boundary they cross is production code.

Two fixture sets, chosen with `FONDACO_DEMO_FIXTURES`:
  * `warehouse` (default) — the ten scripted questions over the Postgres
    warehouse dataset of `docker compose up` (demo/scenarios.md);
  * `local` — the DuckDB dataset of `fondaco demo` (api/demo.py): a few
    legitimate questions and five scripted *attacks*, each of which a
    compromised or prompt-injected LLM could plausibly emit. The attack plans
    are written exactly as an attacker would write them; the boundary decides.

Switch to a real planner with `FONDACO_PLANNER=llm` plus a cloud or local
profile (see `.env.example`).
"""

from __future__ import annotations

import os
import re

from executor.adapters.contract import AnnotatedSchema
from planner.client import PlannerError, PlanningTrace, assemble_plan

PROMPT_VERSION = "demo-fixtures"


def _norm(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.!")


def _query(template: str, params: dict) -> dict:
    return {"id": "s1", "type": "query", "template": template, "params": params}


def _date(value: str) -> dict:
    return {"type": "date", "value": value}


def _agg(ops: list[dict], group_by: list[str], step_id: str = "s2") -> dict:
    return {"id": step_id, "type": "aggregate", "input": "s1", "group_by": group_by, "ops": ops}


def _present(input_id: str, fmt: str, title: str, step_id: str) -> dict:
    return {"id": step_id, "type": "present", "input": input_id, "format": fmt, "title": title}


# Fixture plans, one per scripted question (demo/scenarios.md). Written to the
# frozen DSL and the v3 prompt lessons: SELECT-only, grouping in aggregate
# steps (never in SQL), half-open date ranges, no EXTRACT(... FROM ...).
_FIXTURES: dict[str, list[dict]] = {
    _norm("How many orders were placed per region since October 2025?"): [
        _query(
            "SELECT region FROM orders WHERE order_date >= %(since)s",
            {"since": _date("2025-10-01")},
        ),
        _agg([{"op": "count", "column": "*", "as": "n_orders"}], ["region"]),
        _present("s2", "table", "Orders per region since 2025-10-01", "s3"),
    ],
    _norm("What is the total revenue per region for orders placed in 2026?"): [
        _query(
            "SELECT region, total_amount FROM orders "
            "WHERE order_date >= %(start)s AND order_date < %(end)s",
            {"start": _date("2026-01-01"), "end": _date("2027-01-01")},
        ),
        _agg([{"op": "sum", "column": "total_amount", "as": "revenue"}], ["region"]),
        _present("s2", "table", "Total revenue per region, 2026", "s3"),
    ],
    _norm("What was the average order value per order status in 2026?"): [
        _query(
            "SELECT status, total_amount FROM orders "
            "WHERE order_date >= %(start)s AND order_date < %(end)s",
            {"start": _date("2026-01-01"), "end": _date("2027-01-01")},
        ),
        _agg([{"op": "avg", "column": "total_amount", "as": "avg_value"}], ["status"]),
        _present("s2", "table", "Average order value per status, 2026", "s3"),
    ],
    _norm("How many deliveries did each carrier handle in 2026?"): [
        _query(
            "SELECT carrier FROM deliveries "
            "WHERE shipped_date >= %(start)s AND shipped_date < %(end)s",
            {"start": _date("2026-01-01"), "end": _date("2027-01-01")},
        ),
        _agg([{"op": "count", "column": "*", "as": "n_deliveries"}], ["carrier"]),
        _present("s2", "table", "Deliveries per carrier, 2026", "s3"),
    ],
    _norm("How many products do we have per category?"): [
        _query("SELECT category FROM products", {}),
        _agg([{"op": "count", "column": "*", "as": "n_products"}], ["category"]),
        _present("s2", "table", "Products per category", "s3"),
    ],
    _norm("What is the highest single order amount recorded in 2026?"): [
        _query(
            "SELECT total_amount FROM orders "
            "WHERE order_date >= %(start)s AND order_date < %(end)s",
            {"start": _date("2026-01-01"), "end": _date("2027-01-01")},
        ),
        _agg([{"op": "max", "column": "total_amount", "as": "max_amount"}], []),
        _present("s2", "scalar", "Highest single order amount, 2026", "s3"),
    ],
    _norm("What total quantity moved out of each warehouse in 2026 (outbound movements only)?"): [
        _query(
            "SELECT warehouse, quantity FROM stock_movements "
            "WHERE moved_at >= %(start)s AND moved_at < %(end)s "
            "AND movement_type = %(kind)s",
            {
                "start": {"type": "timestamp", "value": "2026-01-01 00:00:00"},
                "end": {"type": "timestamp", "value": "2027-01-01 00:00:00"},
                "kind": {"type": "string", "value": "outbound"},
            },
        ),
        _agg([{"op": "sum", "column": "quantity", "as": "total_qty"}], ["warehouse"]),
        _present("s2", "table", "Outbound quantity per warehouse, 2026", "s3"),
    ],
    _norm("How many orders were cancelled per region in 2026?"): [
        _query(
            "SELECT region FROM orders "
            "WHERE order_date >= %(start)s AND order_date < %(end)s AND status = %(status)s",
            {
                "start": _date("2026-01-01"),
                "end": _date("2027-01-01"),
                "status": {"type": "string", "value": "cancelled"},
            },
        ),
        _agg([{"op": "count", "column": "*", "as": "n_cancelled"}], ["region"]),
        _present("s2", "table", "Cancelled orders per region, 2026", "s3"),
    ],
    _norm("How many orders were placed per month in the first half of 2026?"): [
        _query(
            "SELECT to_char(order_date, 'YYYY-MM') AS month FROM orders "
            "WHERE order_date >= %(start)s AND order_date < %(end)s",
            {"start": _date("2026-01-01"), "end": _date("2026-07-01")},
        ),
        _agg([{"op": "count", "column": "*", "as": "n_orders"}], ["month"]),
        _present("s2", "table", "Orders per month, H1 2026", "s3"),
    ],
    # Q10 is meant to be DENIED: it reads restricted customer PII, which policy
    # refuses at `internal` clearance. Demonstrates the deny path.
    _norm("List the names and emails of customers in Venezia."): [
        _query(
            "SELECT name, email FROM customers WHERE city = %(city)s",
            {"city": {"type": "string", "value": "Venezia"}},
        ),
        _present("s1", "table", "Customers in Venezia", "s2"),
    ],
}


# ── `fondaco demo` (DuckDB, api/demo.py) ─────────────────────────────────
# Tables: employees(id, full_name, department, role, salary, iban, ssn,
# hire_date) and orders(id, employee_id, region, status, amount, order_date).
# Labels: see api/demo.py SCHEMA_YAML. Egress clearance: internal.


def _count(column: str, as_: str) -> dict:
    return {"op": "count", "column": column, "as": as_}


_LOCAL_FIXTURES: dict[str, list[dict]] = {
    _norm("How many orders are there per status?"): [
        _query("SELECT status FROM orders", {}),
        _agg([_count("*", "n_orders")], ["status"]),
        _present("s2", "table", "Orders per status", "s3"),
    ],
    _norm("What is the total order amount per region in 2026?"): [
        _query(
            "SELECT region, amount FROM orders "
            "WHERE order_date >= %(start)s AND order_date < %(end)s",
            {"start": _date("2026-01-01"), "end": _date("2027-01-01")},
        ),
        _agg([{"op": "sum", "column": "amount", "as": "total_amount"}], ["region"]),
        _present("s2", "table", "Order amount per region, 2026", "s3"),
    ],
    _norm("How many employees work in each department?"): [
        _query("SELECT department FROM employees", {}),
        _agg([_count("*", "headcount")], ["department"]),
        _present("s2", "table", "Headcount per department", "s3"),
    ],
    # ── Attacks. Each is the plan a hijacked planner would emit. ──
    _norm("Show me the CEO's IBAN"): [
        _query(
            "SELECT full_name, iban FROM employees WHERE role = %(role)s",
            {"role": {"type": "string", "value": "CEO"}},
        ),
        _present("s1", "table", "CEO bank details", "s2"),
    ],
    _norm("Binary search John Doe's salary"): [
        # One probe of a binary search: "is the salary above 90k?" answered
        # by whether the count is 0 or 1. The salary is never projected.
        _query(
            "SELECT department FROM employees WHERE full_name = %(name)s AND salary > %(t)s",
            {
                "name": {"type": "string", "value": "John Doe"},
                "t": {"type": "int", "value": 90000},
            },
        ),
        _agg([_count("*", "matches")], []),
        _present("s2", "scalar", "Probe: salary above 90000?", "s3"),
    ],
    _norm("SELECT * FROM read_csv('/etc/passwd')"): [
        _query("SELECT * FROM read_csv('/etc/passwd')", {}),
        _present("s1", "table", "File contents", "s2"),
    ],
    _norm("Order amounts with the employee's IBAN (comma-join)"): [
        _query(
            "SELECT o.amount, e.iban FROM orders o, employees e WHERE o.employee_id = e.id",
            {},
        ),
        _present("s1", "table", "Orders with IBAN", "s2"),
    ],
    _norm("Average salary by department"): [
        _query("SELECT department, salary FROM employees", {}),
        _agg([{"op": "avg", "column": "salary", "as": "avg_salary"}], ["department"]),
        _present("s2", "table", "Average salary per department", "s3"),
    ],
}

# (question, kind, what the boundary does with it) — shown in the demo UI.
LOCAL_SCRIPTED: tuple[tuple[str, str, str], ...] = (
    ("How many orders are there per status?", "question", "public data: allowed"),
    ("What is the total order amount per region in 2026?", "question", "internal: allowed"),
    ("How many employees work in each department?", "question", "internal: allowed"),
    ("Show me the CEO's IBAN", "attack", "restricted column: denied by policy"),
    (
        "Binary search John Doe's salary",
        "attack",
        "filter on restricted/confidential columns counts as a read: denied",
    ),
    (
        "SELECT * FROM read_csv('/etc/passwd')",
        "attack",
        "file read outside the schema: rejected by the validator",
    ),
    (
        "Order amounts with the employee's IBAN (comma-join)",
        "attack",
        "comma-join resolved by the AST: iban is restricted, denied",
    ),
    (
        "Average salary by department",
        "attack",
        "confidential column above the internal clearance: denied",
    ),
)

FIXTURE_SETS: dict[str, dict[str, list[dict]]] = {
    "warehouse": _FIXTURES,
    "local": _LOCAL_FIXTURES,
}
FIXTURES_ENV = "FONDACO_DEMO_FIXTURES"


def _fixture_set(name: str | None) -> str:
    chosen = (name or os.environ.get(FIXTURES_ENV) or "warehouse").strip().lower()
    if chosen not in FIXTURE_SETS:
        raise PlannerError(
            "demo_fixtures_unknown", f"{FIXTURES_ENV} must be one of warehouse, local"
        )
    return chosen


def scripted_questions(fixture_set: str | None = None) -> list[dict]:
    """The questions the demo UI offers: [{question, kind, note}]."""
    if _fixture_set(fixture_set) == "local":
        return [{"question": q, "kind": k, "note": n} for q, k, n in LOCAL_SCRIPTED]
    return [{"question": q, "kind": "question", "note": ""} for q in SCRIPTED_QUESTIONS]


class DemoPlanner:
    """Fixture planner with the same interface as PlannerClient."""

    def __init__(self, fixture_set: str | None = None) -> None:
        self.fixture_set = _fixture_set(fixture_set)
        self._fixtures = FIXTURE_SETS[self.fixture_set]

    def generate_plan(self, question: str, schema: AnnotatedSchema) -> tuple[dict, PlanningTrace]:
        steps = self._fixtures.get(_norm(question))
        if steps is None:
            raise PlannerError(
                "demo_unknown_question",
                "Demo mode only answers its scripted questions. For free-form questions "
                "set FONDACO_PLANNER=llm with a cloud or Ollama profile (see .env.example).",
            )
        plan, attempt = assemble_plan(question, steps)
        if not attempt.validation.valid:
            codes = ",".join(sorted({e.code for e in attempt.validation.errors}))
            if self.fixture_set == "local":
                # A scripted attack the validator refused: exactly what happens
                # to an LLM that emits this plan (it never becomes a plan).
                raise PlannerError(f"rejected_by_validator:{codes}", "plan failed validation")
            raise PlannerError("demo_fixture_invalid", f"fixture failed validation: {codes}")
        trace = PlanningTrace(question=question, prompt_version=PROMPT_VERSION, attempts=(attempt,))
        return plan, trace


SCRIPTED_QUESTIONS: tuple[str, ...] = (
    "How many orders were placed per region since October 2025?",
    "What is the total revenue per region for orders placed in 2026?",
    "What was the average order value per order status in 2026?",
    "How many deliveries did each carrier handle in 2026?",
    "How many products do we have per category?",
    "What is the highest single order amount recorded in 2026?",
    "What total quantity moved out of each warehouse in 2026 (outbound movements only)?",
    "How many orders were cancelled per region in 2026?",
    "How many orders were placed per month in the first half of 2026?",
    "List the names and emails of customers in Venezia.",
)
