# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Cardinality thresholds and per-session query budgets.

Honest mitigation, not a solution: a *sequence* of individually legal
aggregate questions can binary-search a single row's value. These guards
raise the cost of that attack and make it visible in the audit log; they
do not close the channel (see README "What this does NOT protect
against" and design/label-model.md §6).

Two guards:

1. **k-threshold** — an aggregate group computed from fewer than k input
   rows is dropped entirely. Dropping, not masking: a masked cell beside
   a visible count still leaks the count. This kills the binary-search
   primitive (a count over a one-row filtered set).
2. **Query budget** — a per-session hard stop on executed query steps.
   Exceeding it refuses the plan; nothing partial runs.

Boundary code: fail closed everywhere. A configuration value we cannot
parse, or that asks for weaker protection than the default, is refused —
never honored, never silently disabled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_K = 5
DEFAULT_QUERY_BUDGET = 20

GUARD_K_THRESHOLD = "k_threshold"
GUARD_QUERY_BUDGET = "query_budget"


class BudgetExceeded(Exception):
    """Raised when a session's query budget is exhausted (hard stop)."""

    def __init__(self, used: int, limit: int) -> None:
        self.used = used
        self.limit = limit
        super().__init__(f"query budget exhausted: {used}/{limit}")


@dataclass(frozen=True)
class GuardConfig:
    k: int = DEFAULT_K
    query_budget: int = DEFAULT_QUERY_BUDGET


@dataclass(frozen=True)
class SuppressionResult:
    rows: tuple[tuple, ...]
    suppressed: int


@dataclass(frozen=True)
class BudgetState:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def _positive_int(raw: str | None, default: int) -> int:
    """Parse an env value; unparsable or guard-disabling values → the default.

    Operators may tune a guard (any value ≥ 1), but they cannot turn one
    off: `k=0`, a negative budget, or garbage falls back to the default
    rather than to "no protection".
    """
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 1:  # 0 or negative would disable the guard — refuse, fail closed
        return default
    return value


def config_from_env() -> GuardConfig:
    return GuardConfig(
        k=_positive_int(os.environ.get("FONDACO_GUARD_K"), DEFAULT_K),
        query_budget=_positive_int(os.environ.get("FONDACO_QUERY_BUDGET"), DEFAULT_QUERY_BUDGET),
    )


def suppress_small_groups(
    rows: tuple[tuple, ...], group_sizes: tuple[int, ...], k: int
) -> SuppressionResult:
    """Drop every aggregate group backed by fewer than k input rows.

    `group_sizes[i]` is the number of input rows that produced `rows[i]`.
    A mismatch between the two is a programming error inside the
    boundary: suppress everything rather than risk leaking a small group.
    """
    if k < 1 or len(rows) != len(group_sizes):
        return SuppressionResult(rows=(), suppressed=len(rows))
    kept = tuple(row for row, size in zip(rows, group_sizes, strict=True) if size >= k)
    return SuppressionResult(rows=kept, suppressed=len(rows) - len(kept))


class QueryBudget:
    """Per-session hard stop on executed query steps. In-memory (V1 demo)."""

    def __init__(self, limit: int = DEFAULT_QUERY_BUDGET) -> None:
        self._limit = max(1, limit)
        self._used: dict[str, int] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def state(self, session_id: str) -> BudgetState:
        return BudgetState(used=self._used.get(session_id, 0), limit=self._limit)

    def consume(self, session_id: str, n_query_steps: int) -> BudgetState:
        """Charge n query steps to a session, or raise BudgetExceeded.

        All-or-nothing: a refused request consumes nothing, so a rejected
        plan cannot silently eat the remaining budget.
        """
        if n_query_steps < 0:
            raise BudgetExceeded(self._used.get(session_id, 0), self._limit)
        used = self._used.get(session_id, 0)
        if used + n_query_steps > self._limit:
            raise BudgetExceeded(used, self._limit)
        self._used[session_id] = used + n_query_steps
        return BudgetState(used=used + n_query_steps, limit=self._limit)
