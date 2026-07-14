# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Deterministic demo planner — pre-generated fixture plans, no LLM.

Powers the keyless `docker compose up` demo. It is transparently a fixture:
it returns hand-written plans for the ten scripted questions and nothing
else. Crucially it is NOT a shortcut around the boundary — every fixture
plan is built and validated through `planner.client.assemble_plan`, the
exact same path the live LLM planner uses, and then flows through the same
policy engine, executor, guards, and audit log. The plans are fixtures;
the boundary they cross is production code.

Switch to a real planner with `FONDACO_PLANNER=llm` plus a cloud or local
profile (see `.env.example`).
"""

from __future__ import annotations

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


class DemoPlanner:
    """Fixture planner with the same interface as PlannerClient."""

    def generate_plan(self, question: str, schema: AnnotatedSchema) -> tuple[dict, PlanningTrace]:
        steps = _FIXTURES.get(_norm(question))
        if steps is None:
            raise PlannerError(
                "demo_unknown_question",
                "Demo mode only answers the scripted questions in demo/scenarios.md. "
                "For free-form questions set FONDACO_PLANNER=llm with a cloud or Ollama "
                "profile (see .env.example).",
            )
        plan, attempt = assemble_plan(question, steps)
        if not attempt.validation.valid:  # a broken fixture is a bug, fail loud
            codes = ",".join(sorted({e.code for e in attempt.validation.errors}))
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
