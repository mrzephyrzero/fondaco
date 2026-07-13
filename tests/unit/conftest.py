# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
import copy

import pytest

VALID_PLAN = {
    "dsl_version": "v0",
    "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
    "question": "How many orders per region since 2026-01-01?",
    "steps": [
        {
            "id": "s1",
            "type": "query",
            "template": "SELECT region, status FROM orders WHERE order_date >= %(since)s",
            "params": {"since": {"type": "date", "value": "2026-01-01"}},
        },
        {
            "id": "s2",
            "type": "aggregate",
            "input": "s1",
            "group_by": ["region"],
            "ops": [{"op": "count", "column": "*", "as": "n_orders"}],
        },
        {
            "id": "s3",
            "type": "present",
            "input": "s2",
            "format": "table",
            "title": "Orders per region since 2026-01-01",
        },
    ],
}


@pytest.fixture
def valid_plan() -> dict:
    return copy.deepcopy(VALID_PLAN)
