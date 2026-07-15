# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Deterministic executor for validated plans (design/plan-dsl.md).

No LLM involvement: steps run exactly as written. Every derived result
carries the max label of its inputs (design/label-model.md §4 — for the
linear v0 DSL each aggregate/present has exactly one input, so max-label
propagation reduces to inheriting it; aggregation never declassifies).
Faults raise ExecutorError with sanitized detail; there are no partial
results.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from boundary.guards import DEFAULT_K, suppress_small_groups
from boundary.validator import validate_plan
from executor.adapters.contract import Adapter, AdapterError, LabeledResult


class ExecutorError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class RunResult:
    title: str
    format: str
    columns: tuple[str, ...]
    rows: tuple[tuple, ...]
    label: str
    digest: str
    suppressed_groups: int = 0  # dropped by the k-threshold guard


def _digest(columns: tuple[str, ...], rows: tuple[tuple, ...]) -> str:
    canonical = json.dumps([list(columns), [list(r) for r in rows]], default=str, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _numeric(value: object, op: str, column: str) -> float | int | Decimal:
    # Decimal covers Postgres numeric/money columns; bool is excluded explicitly.
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise ExecutorError("non_numeric_aggregate", f"{op} on non-numeric column {column!r}")
    return value


def _aggregate(step: dict, source: LabeledResult, k: int) -> tuple[LabeledResult, int]:
    for name in [*step["group_by"], *(op["column"] for op in step["ops"])]:
        if name != "*" and name not in source.columns:
            raise ExecutorError("unknown_column", f"column {name!r} not in input result")

    index = {name: i for i, name in enumerate(source.columns)}
    groups: dict[tuple, list[tuple]] = {}
    for row in source.rows:
        key = tuple(row[index[g]] for g in step["group_by"])
        groups.setdefault(key, []).append(row)
    if not step["group_by"] and not groups:
        groups[()] = []  # global aggregate over an empty result still yields one row

    out_columns = tuple(step["group_by"]) + tuple(op["as"] for op in step["ops"])
    out_rows = []
    group_sizes = []
    for key in sorted(groups, key=lambda g: tuple(str(v) for v in g)):
        members = groups[key]
        group_sizes.append(len(members))
        computed: list[object] = list(key)
        for op in step["ops"]:
            kind, column = op["op"], op["column"]
            if kind == "count":
                values = (
                    members
                    if column == "*"
                    else [r for r in members if r[index[column]] is not None]
                )
                computed.append(len(values))
                continue
            cells = [r[index[column]] for r in members if r[index[column]] is not None]
            if kind in ("sum", "avg"):
                numbers = [_numeric(c, kind, column) for c in cells]
                if kind == "sum":
                    computed.append(sum(numbers))
                else:
                    computed.append(sum(numbers) / len(numbers) if numbers else None)
            elif kind in ("min", "max"):
                try:
                    computed.append((min if kind == "min" else max)(cells) if cells else None)
                except TypeError as exc:
                    raise ExecutorError(
                        "incomparable_values", f"{kind} on mixed-type column {column!r}"
                    ) from exc
            else:  # unreachable for validated plans; fail closed anyway
                raise ExecutorError("unknown_op", f"aggregate op {kind!r}")
        out_rows.append(tuple(computed))

    columns = out_columns
    # k-threshold: groups backed by fewer than k input rows never leave the
    # boundary — this is where the binary-search primitive dies (Phase 5).
    suppression = suppress_small_groups(tuple(out_rows), tuple(group_sizes), k)
    rows = suppression.rows
    result = LabeledResult(
        columns=columns,
        rows=rows,
        label=source.label,  # aggregation never declassifies (label-model.md §4)
        row_count=len(rows),
        digest=_digest(columns, rows),
    )
    return result, suppression.suppressed


def run_plan(plan: dict, adapter: Adapter, k: int = DEFAULT_K) -> RunResult:
    """Execute a plan. Re-validates first; refuses non-read-only adapters.

    `k` is the cardinality threshold applied to every aggregate result.
    """
    validation = validate_plan(plan)
    if not validation.valid:
        codes = ",".join(sorted({e.code for e in validation.errors}))
        raise ExecutorError("invalid_plan", f"refusing unvalidated plan: {codes}")

    capabilities = adapter.capabilities()
    if capabilities.read_only is not True:
        raise ExecutorError("adapter_not_read_only", "contract §2.3 requires read_only=True")
    if plan["dsl_version"] not in capabilities.dsl_versions:
        raise ExecutorError("dsl_version_unsupported", plan["dsl_version"])

    results: dict[str, LabeledResult] = {}
    suppressed = 0
    try:
        for step in plan["steps"]:
            if step["type"] == "query":
                results[step["id"]] = adapter.execute(step)
            elif step["type"] == "aggregate":
                results[step["id"]], dropped = _aggregate(step, results[step["input"]], k)
                suppressed += dropped
            else:  # present — validator guarantees it is last and unique
                source = results[step["input"]]
                return RunResult(
                    title=step["title"],
                    format=step["format"],
                    columns=source.columns,
                    rows=source.rows,
                    label=source.label,
                    digest=source.digest,
                    suppressed_groups=suppressed,
                )
    except AdapterError as exc:
        raise ExecutorError("adapter_error", f"{exc.kind}: {exc.message}") from exc
    except ExecutorError:
        raise
    except Exception as exc:  # fail closed: no partial results ever escape
        raise ExecutorError("executor_fault", type(exc).__name__) from exc
    raise ExecutorError("no_present_step", "plan ended without present")  # unreachable if valid
