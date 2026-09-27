# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
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


# ── Fail-closed faults: nothing partial ever escapes ───────────────────────
#
# Every path below ends in an ExecutorError and never in a RunResult, so a
# fault can never return a half-computed answer. The adapter faults are
# simulated here; tests/integration/test_postgres_adapter.py raises the real
# ones against Postgres.


class FaultingAdapter(FakeAdapter):
    """Raises the given exception from execute(), after passing every gate."""

    def __init__(self, exc: Exception):
        super().__init__(_result(ROWS))
        self._exc = exc

    def execute(self, step):
        raise self._exc


class VersionedAdapter(FakeAdapter):
    """A read-only adapter that does not speak the plan's DSL version."""

    def capabilities(self):
        caps = super().capabilities()
        return Capabilities(
            dsl_versions=("v9",),
            param_types=caps.param_types,
            max_rows=caps.max_rows,
            read_only=caps.read_only,
        )


def test_unexpected_adapter_fault_is_executor_fault(valid_plan):
    # Not an AdapterError: a bug, or a driver raising something undocumented.
    # The catch-all turns it into a typed error carrying the type name only.
    adapter = FaultingAdapter(RuntimeError("customer 4111-1111-1111-1111"))
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(valid_plan, adapter)
    assert excinfo.value.code == "executor_fault"
    assert excinfo.value.detail == "RuntimeError"
    assert "4111" not in str(excinfo.value)


def test_adapter_error_is_wrapped_in_executor_error(valid_plan):
    # Callers of run_plan handle one error type; the adapter's kind survives.
    from executor.adapters.contract import AdapterError

    adapter = FaultingAdapter(AdapterError("timeout", "QueryCanceled (sqlstate=57014)"))
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(valid_plan, adapter)
    assert excinfo.value.code == "adapter_error"
    assert excinfo.value.detail == "timeout: QueryCanceled (sqlstate=57014)"


@pytest.mark.parametrize("op", ["min", "max"])
def test_min_max_over_mixed_types_fails_closed(op):
    # Python cannot order an int against a str; the aggregate refuses rather
    # than guess. Raised before the k-threshold, so k plays no part.
    mixed = [("north", 10), ("north", "ten")]
    plan = _plan_with_ops([{"op": op, "column": "amount", "as": "v"}])
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(plan, FakeAdapter(_result(mixed)), k=1)
    assert excinfo.value.code == "incomparable_values"


def test_adapter_that_does_not_speak_the_dsl_version_is_refused(valid_plan):
    # The validator pins dsl_version to v0, so this can only trip on the
    # adapter side — and it trips before any step runs.
    with pytest.raises(ExecutorError) as excinfo:
        run_plan(valid_plan, VersionedAdapter(_result(ROWS)))
    assert excinfo.value.code == "dsl_version_unsupported"
    assert excinfo.value.detail == "v0"
