# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 4 — API + approval flow + audit UI: **COMPLETE**
- **Last completed checkpoint:** **P4** (2026-07-14) — all items pass; CI green on GitHub Actions for commit `dc5f82c`
- **Next action:** fresh session for **Phase 5 — Guards: cardinality thresholds and query budgets**. Context: `CLAUDE.md`, this file, `design/label-model.md`, Phase 1 policy engine (`boundary/policy.py`).

## Checkpoint P4 status

| Item | Status |
|---|---|
| Full loop demo-able in < 2 minutes from `docker compose up` | ✅ **64.7s** measured (compose up 17.8s + real LLM planning via host Ollama 46.7s + approve/execute), warm model. First-ever request adds ~2 min one-time Ollama model load; a funded cloud API planner takes seconds |
| Rejected plan provably never executed | ✅ `tests/unit/test_api.py::test_rejected_plan_is_provably_never_executed`: counting adapter shows 0 executions after reject and after post-reject approve (409); policy-denied plans equally unapprovable |
| Audit view shows complete chain for every request | ✅ six-event chain per request rendered at `/audit` with verified-chain banner, event/plan filters, per-plan links; chains survive restarts (named volume). Unit-asserted + manually verified over two demo runs |

Verification 2026-07-14: 72 unit + 17 integration tests green, ruff clean.

## What Phase 4 added

`api/main.py` app factory (ask → plan page with per-step labels + policy reasoning → approve/reject → labeled results); `boundary.policy.step_labels` public helper; `AuditLog.entries()` read view; `PlanningTrace` wired into audit (per-attempt validation events); Jinja templates + vendored htmx 2.0.4; compose: LLM env (host-Ollama default), audit volume.

## Notes for Phase 5 (and the human)

- Guards hook points: `boundary/guards.py` still a stub. k-threshold applies to aggregates flowing back toward the planner in repair loops — note the current repair loop (planner/client.py) feeds back only *validation errors*, never results; the guard surface will matter when repair-on-execution or iterative planning appears. Per-session query budget: natural enforcement point is `create_app`'s ask/approve handlers + runner.
- Approval identity is self-declared (no auth in V1, DECISIONS.md) — worth an explicit line in the Phase 7 threat model.
- Anthropic credits still pending (open question) — compose demo defaults to host Ollama meanwhile.

## Open questions (for the human)

1. **Anthropic credits** — top up to switch the demo planner to `claude-sonnet-5` (faster + stronger than local 7B).
2. **Local Python** — 3.13/3.11 locally vs pinned 3.12 in CI/Docker. Fine, or install 3.12 for parity?

## INTERFACE_CHANGE_REQUEST

_None._
