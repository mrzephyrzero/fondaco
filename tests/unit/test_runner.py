# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Runner: deterministic execution, label inheritance, fail-closed faults."""

import pytest

from executor.adapters.contract import Capabilities, LabeledResult
from executor.runner import ExecutorError, run_plan


class FakeAdapter:
    """In-memory adapter returning a canned labeled result for any query."""

    def __init__(self, result: LabeledResult, read_only: bool = True):
        self._result = result
        self._read_only = read_only

    def get_schema(self):
        raise NotImplementedError

    def execute(self, step):
        return self._result

    def capabilities(self):
        return Capabilities(
            dsl_versions=("v0",),
            param_types=("string", "int", "float", "bool", "date", "timestamp"),
            max_rows=10_000,
            read_only=self._read_only,
        )


def _result(rows, columns=("region", "amount"), label="internal"):
    return LabeledResult(
        columns=columns, rows=tuple(rows), label=label, row_count=len(rows), digest="d" * 64
    )


ROWS = [
    ("north", 10),
    ("north", 30),
    ("south", 5),
]


def _plan_with_ops(ops, group_by=("region",)):
    plan = {
        "dsl_version": "v0",
        "plan_id": "3f2b8c9e-1d4a-4f6b-8a2c-9e7d5b3a1c0f",
        "question": "q?",
        "steps": [
            {
                "id": "s1",
                "type": "query",
                "template": "SELECT region, amount FROM orders",
                "params": {},
            },
            {
                "id": "s2",
                "type": "aggregate",
                "input": "s1",
                "group_by": list(group_by),
                "ops": ops,
            },
            {"id": "s3", "type": "present", "input": "s2", "format": "table", "title": "t"},
        ],
    }
    return plan


def test_end_to_end_with_fake_adapter():
    plan = _plan_with_ops(
        [
            {"op": "count", "column": "*", "as": "n"},
            {"op": "sum", "column": "amount", "as": "total"},
            {"op": "avg", "column": "amount", "as": "mean"},
        ]
    )
    # k=1 disables small-group suppression so aggregate math can be checked.
    result = run_plan(plan, FakeAdapter(_result(ROWS)), k=1)
    assert result.columns == ("region", "n", "total", "mean")
    assert result.rows == (("north", 2, 40, 20.0), ("south", 1, 5, 5.0))
    assert result.label == "internal"
    assert result.title == "t"
    assert len(result.digest) == 64
    assert result.suppressed_groups == 0


def test_label_propagates_unchanged_through_aggregate_and_present():
    plan = _plan_with_ops([{"op": "min", "column": "amount", "as": "lo"}])
    result = run_plan(plan, FakeAdapter(_result(ROWS, label="restricted")), k=1)
    assert result.label == "restricted"  # aggregation never declassifies


def test_global_aggregate_without_group_by():
    plan = _plan_with_ops([{"op": "max", "column": "amount", "as": "hi"}], group_by=())
    result = run_plan(plan, FakeAdapter(_result(ROWS)), k=1)
    assert result.rows == ((30,),)


def test_small_groups_suppressed_at_default_k():
    # north (2 rows) and south (1 row) are both below k=5 → dropped.
    plan = _plan_with_ops([{"op": "count", "column": "*", "as": "n"}])
    result = run_plan(plan, FakeAdapter(_result(ROWS)), k=5)
    assert result.rows == ()
    assert result.suppressed_groups == 2


def test_invalid_plan_refused():
    with pytest.raises(ExecutorError) as excinfo:
        run_plan({"not": "a plan"}, FakeAdapter(_result(ROWS)))
    assert excinfo.value.code == "invalid_plan"


def test_non_read_only_adapter_refused(valid_plan):
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(valid_plan, FakeAdapter(_result(ROWS), read_only=False))
    assert excinfo.value.code == "adapter_not_read_only"


def test_sum_on_text_fails_closed():
    plan = _plan_with_ops([{"op": "sum", "column": "region", "as": "oops"}])
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(plan, FakeAdapter(_result(ROWS)))
    assert excinfo.value.code == "non_numeric_aggregate"


def test_unknown_column_fails_closed():
    plan = _plan_with_ops([{"op": "sum", "column": "ghost", "as": "oops"}])
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(plan, FakeAdapter(_result(ROWS)))
    assert excinfo.value.code == "unknown_column"


def test_valid_plan_fixture_runs(valid_plan):
    result = run_plan(valid_plan, FakeAdapter(_result(ROWS, columns=("region", "status"))), k=1)
    assert result.label == "internal"
    assert result.columns == ("region", "n_orders")
