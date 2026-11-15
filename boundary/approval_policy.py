# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Risk-proportional approval: which allowed plans may skip the human.

The default is unchanged from V1: every plan waits for a human
(`pending_approval`). A deployer can opt in to auto-approval of low-risk
plans. A plan is `auto_approved` only if *every* criterion holds:

  1. auto-approval is enabled (`FONDACO_AUTO_APPROVE_ENABLED`, default off);
  2. the policy engine allowed it (a deny is never approvable, by anyone);
  3. its result label is ≤ `auto_approve_max_label` (default `internal`;
     `restricted` can never be configured — it falls back to the default);
  4. every query step was resolved by the analyser — no relation or column
     the label was forced to `restricted` for, no fallback of any kind;
  5. the session's query budget can pay for all its query steps.

Auto-approval decides *execution*, not *release*. After the run, a result
with fewer than `auto_approve_min_rows` rows (counted after the k-threshold)
is held back and the plan returns to `pending_approval`: small results are
where inference risk concentrates, so a human looks before they leave.

Everything is audited exactly like a human decision; the actor is
`auto-approval-policy`. This module decides, it never executes. It never
raises: any fault means `pending_approval`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from boundary.policy import Label, LabelError, PolicyDecision, analyze_query

AUTO_APPROVED = "auto_approved"
PENDING_APPROVAL = "pending_approval"
AUTO_APPROVAL_ACTOR = "auto-approval-policy"

DEFAULT_MAX_LABEL = Label.INTERNAL
DEFAULT_MIN_ROWS = 10


@dataclass(frozen=True)
class ApprovalPolicy:
    auto_approve_enabled: bool = False
    auto_approve_max_label: Label = DEFAULT_MAX_LABEL
    auto_approve_min_rows: int = DEFAULT_MIN_ROWS

    def __post_init__(self) -> None:
        # Fail closed on hostile construction, mirroring config_from_env.
        if not isinstance(self.auto_approve_enabled, bool):
            object.__setattr__(self, "auto_approve_enabled", False)
        if (
            not isinstance(self.auto_approve_max_label, Label)
            or self.auto_approve_max_label >= Label.RESTRICTED
        ):
            object.__setattr__(self, "auto_approve_max_label", DEFAULT_MAX_LABEL)
        if (
            not isinstance(self.auto_approve_min_rows, int)
            or isinstance(self.auto_approve_min_rows, bool)
            or self.auto_approve_min_rows < 1
        ):
            object.__setattr__(self, "auto_approve_min_rows", DEFAULT_MIN_ROWS)


@dataclass(frozen=True)
class ApprovalDecision:
    status: str  # AUTO_APPROVED or PENDING_APPROVAL
    reasons: tuple[str, ...] = field(default_factory=tuple)  # machine-readable

    @property
    def auto(self) -> bool:
        return self.status == AUTO_APPROVED


def policy_from_env(env: dict | None = None) -> ApprovalPolicy:
    """Read the three settings. Anything malformed → the safe default."""
    env = os.environ if env is None else env
    enabled = env.get("FONDACO_AUTO_APPROVE_ENABLED", "").strip().lower() in ("1", "true", "yes")
    try:
        max_label = Label.parse(env.get("FONDACO_AUTO_APPROVE_MAX_LABEL", "internal").strip())
    except (LabelError, AttributeError):
        max_label = DEFAULT_MAX_LABEL
    try:
        min_rows = int(env.get("FONDACO_AUTO_APPROVE_MIN_ROWS", str(DEFAULT_MIN_ROWS)))
    except ValueError:
        min_rows = DEFAULT_MIN_ROWS
    return ApprovalPolicy(enabled, max_label, min_rows)


def decide(
    policy: ApprovalPolicy,
    plan: dict,
    decision: PolicyDecision,
    schema_labels: dict,
    budget_remaining: int,
    dialect: str | None = None,
) -> ApprovalDecision:
    """Pre-execution: may this plan run without a human? Never raises."""
    try:
        return _decide(policy, plan, decision, schema_labels, budget_remaining, dialect)
    except Exception as exc:  # fail closed: a fault means a human decides
        return ApprovalDecision(PENDING_APPROVAL, (f"approval_policy_error:{type(exc).__name__}",))


def _decide(policy, plan, decision, schema_labels, budget_remaining, dialect) -> ApprovalDecision:
    if not policy.auto_approve_enabled:
        return ApprovalDecision(PENDING_APPROVAL, ("auto_approve_disabled",))
    if not decision.allow:
        return ApprovalDecision(PENDING_APPROVAL, ("policy_denied",))

    reasons: list[str] = []
    try:
        label = Label.parse(decision.plan_label)
    except LabelError:
        return ApprovalDecision(PENDING_APPROVAL, ("unknown_plan_label",))
    if label > policy.auto_approve_max_label:
        reasons.append("label_above_auto_approve_max")

    query_steps = [s for s in plan["steps"] if s["type"] == "query"]
    for step in query_steps:
        analysis = analyze_query(step["template"], schema_labels, dialect)
        if not analysis.resolved or analysis.unknown_relations:
            reasons.append(f"unresolved_reference:{step['id']}")
    if len(query_steps) > budget_remaining:
        reasons.append("budget_insufficient")

    if reasons:
        return ApprovalDecision(PENDING_APPROVAL, tuple(reasons))
    return ApprovalDecision(AUTO_APPROVED, ("all_criteria_met",))


def release_allowed(policy: ApprovalPolicy, row_count: object) -> bool:
    """Post-execution: may an auto-approved result leave without a human?"""
    if isinstance(row_count, bool) or not isinstance(row_count, int):
        return False
    return row_count >= policy.auto_approve_min_rows
