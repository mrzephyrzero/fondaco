# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 5 — Guards: **complete pending CI confirmation** (checkpoint below)
- **Last completed checkpoint:** **P5** (2026-07-14) — pending only the pushed CI run turning green (all items verified locally)
- **Next action:** fresh session for **Phase 6 — Packaging & demo polish**. Context: `CLAUDE.md`, this file, `demo/scenarios.md`, README.

## Checkpoint P5 status

| Item | Status |
|---|---|
| Attack simulation halted by budget; audit log flags the pattern | ✅ `tests/adversarial/test_binary_search_attack.py` (2 tests, real Postgres via API): every one-row `count` probe suppressed by k-threshold, run hard-stops at the query budget (429, DB never touched past the limit), `guard_decision` audit entries assert both guards fired and are visible at `/audit` |
| Guards documented in README "What this does NOT protect against" | ✅ drafted (aggregate channel raised-not-closed, no-auth/cookie budget reset, cross-grouping differencing, malicious approver, side channels) — finalized in Phase 7 |

Verification 2026-07-14: 111 tests green (92 unit + 17 integration + 2 adversarial), ruff clean; running container serves the budget UI and `guard_decision` audit filter.

## What Phase 5 added

`boundary/guards.py` — `suppress_small_groups` (k-threshold, drop not mask), `QueryBudget` (per-session hard stop, all-or-nothing), `config_from_env` (fail closed: k<1/negative/garbage → default, guard cannot be disabled). Wired: `executor/runner.py` suppresses every aggregate (new `RunResult.suppressed_groups`); `api/main.py` charges the budget before execution, emits new `guard_decision` audit event, sets a `fondaco_session` cookie; UI shows budget used/limit and suppression notices. `boundary/audit.py` gains `EVENT_GUARD_DECISION`.

## Notes for Phase 6 (and the human)

- **Scope note (logged in DECISIONS.md):** the k-threshold guards *every* aggregate result, not just planner-facing ones — the plan text's "toward the planner in repair loops" scope would guard nothing here, since the repair loop feeds back only validation errors (P3 canary). The reader in the UI is the attacker.
- Demo tuning: default k=5 means a legitimate question whose grouping yields a <5-row group shows a suppression notice — worth surfacing in the Phase 6 walkthrough as a feature, and picking scenario questions whose groups are comfortably above k.
- README now has an architecture-adjacent "Guards" + "What this does NOT protect against" section; Phase 6 adds the ≤1-screen architecture overview above install steps.

## Open questions (for the human)

1. **Anthropic credits** — still low; demo planner defaults to host Ollama. Top up to use `claude-sonnet-5`.
2. **Local Python** — 3.13/3.11 locally vs pinned 3.12 in CI/Docker. Fine, or install 3.12 for parity?

## INTERFACE_CHANGE_REQUEST

_None._
