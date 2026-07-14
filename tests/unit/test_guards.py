# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Guards: k-threshold suppression, query budget hard stop, hostile config."""

import pytest

from boundary.guards import (
    DEFAULT_K,
    DEFAULT_QUERY_BUDGET,
    BudgetExceeded,
    GuardConfig,
    QueryBudget,
    _positive_int,
    config_from_env,
    suppress_small_groups,
)


def test_group_below_k_is_dropped_at_k_minus_one():
    rows = (("north", 4), ("south", 5), ("east", 100))
    sizes = (4, 5, 100)  # north backed by 4 rows < k=5
    result = suppress_small_groups(rows, sizes, k=5)
    assert result.suppressed == 1
    assert ("north", 4) not in result.rows
    assert ("south", 5) in result.rows and ("east", 100) in result.rows


def test_group_at_exactly_k_is_kept():
    rows = (("x", 1),)
    result = suppress_small_groups(rows, (5,), k=5)
    assert result.suppressed == 0
    assert result.rows == rows


def test_global_single_row_aggregate_is_suppressed():
    # count over a one-row filtered set — the binary-search primitive.
    rows = ((1,),)
    result = suppress_small_groups(rows, (1,), k=5)
    assert result.rows == ()
    assert result.suppressed == 1


def test_size_mismatch_suppresses_everything():
    rows = (("a", 1), ("b", 2))
    result = suppress_small_groups(rows, (10,), k=5)  # wrong length
    assert result.rows == ()
    assert result.suppressed == 2


def test_k_below_one_suppresses_everything():
    rows = (("a", 999),)
    result = suppress_small_groups(rows, (999,), k=0)
    assert result.rows == ()


def test_budget_allows_up_to_limit_then_hard_stops():
    budget = QueryBudget(limit=3)
    assert budget.consume("s", 1).used == 1
    assert budget.consume("s", 2).used == 3
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.consume("s", 1)
    assert excinfo.value.used == 3 and excinfo.value.limit == 3


def test_budget_refusal_consumes_nothing():
    budget = QueryBudget(limit=2)
    budget.consume("s", 2)
    with pytest.raises(BudgetExceeded):
        budget.consume("s", 1)
    # A refused charge must not advance the counter.
    assert budget.state("s").used == 2
    assert budget.state("s").remaining == 0


def test_budget_is_per_session():
    budget = QueryBudget(limit=1)
    budget.consume("alice", 1)
    assert budget.consume("bob", 1).used == 1  # bob unaffected by alice


def test_negative_charge_is_refused():
    budget = QueryBudget(limit=5)
    with pytest.raises(BudgetExceeded):
        budget.consume("s", -3)
    assert budget.state("s").used == 0


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("10", 5, 10),
        ("3", 5, 3),
        ("0", 5, 5),  # disabling refused → default
        ("-1", 20, 20),  # negative refused → default
        ("garbage", 5, 5),
        ("", 5, 5),
        (None, 7, 7),
    ],
)
def test_positive_int_fails_closed(raw, default, expected):
    assert _positive_int(raw, default) == expected


def test_config_from_env_defaults(monkeypatch):
    monkeypatch.delenv("FONDACO_GUARD_K", raising=False)
    monkeypatch.delenv("FONDACO_QUERY_BUDGET", raising=False)
    config = config_from_env()
    assert config == GuardConfig(k=DEFAULT_K, query_budget=DEFAULT_QUERY_BUDGET)


def test_config_from_env_cannot_disable_guards(monkeypatch):
    monkeypatch.setenv("FONDACO_GUARD_K", "0")
    monkeypatch.setenv("FONDACO_QUERY_BUDGET", "-5")
    config = config_from_env()
    assert config.k == DEFAULT_K
    assert config.query_budget == DEFAULT_QUERY_BUDGET
