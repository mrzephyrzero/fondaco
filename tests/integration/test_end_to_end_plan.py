# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Checkpoint P2: hand-written plans (no LLM) run end-to-end with labels."""

from tests.integration.conftest import requires_db

pytestmark = requires_db

HAND_WRITTEN_PLAN = {
    "dsl_version": "v0",
    "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
    "question": "How many orders and total revenue per region since 2025-10-01?",
    "steps": [
        {
            "id": "s1",
            "type": "query",
            "template": ("SELECT region, total_amount FROM orders WHERE order_date >= %(since)s"),
            # ~6k of 20k orders fall after this date — safely under max_rows=10 000;
            # the raw-row cap is the adapter's guard, tested separately.
            "params": {"since": {"type": "date", "value": "2025-10-01"}},
        },
        {
            "id": "s2",
            "type": "aggregate",
            "input": "s1",
            "group_by": ["region"],
            "ops": [
                {"op": "count", "column": "*", "as": "n_orders"},
                {"op": "sum", "column": "total_amount", "as": "revenue"},
            ],
        },
        {
            "id": "s3",
            "type": "present",
            "input": "s2",
            "format": "table",
            "title": "Orders per region since 2025-10-01",
        },
    ],
}


def test_hand_written_plan_runs_end_to_end(adapter):
    from executor.runner import run_plan

    result = run_plan(HAND_WRITTEN_PLAN, adapter)
    assert result.columns == ("region", "n_orders", "revenue")
    assert set(r[0] for r in result.rows) == {"east", "north", "south", "west"}
    assert all(r[1] > 0 and r[2] > 0 for r in result.rows)
    assert result.label == "internal"
    assert len(result.digest) == 64


def test_multi_step_label_propagation_on_restricted_table(adapter):
    from executor.runner import run_plan

    plan = {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "How many customers per city?",
        "steps": [
            {
                "id": "s1",
                "type": "query",
                "template": "SELECT city FROM customers",
                "params": {},
            },
            {
                "id": "s2",
                "type": "aggregate",
                "input": "s1",
                "group_by": ["city"],
                "ops": [{"op": "count", "column": "*", "as": "n"}],
            },
            {"id": "s3", "type": "present", "input": "s2", "format": "table", "title": "t"},
        ],
    }
    result = run_plan(plan, adapter)
    # customers is restricted; the aggregate must not declassify the count.
    assert result.label == "restricted"
    assert sum(r[1] for r in result.rows) == 1000
