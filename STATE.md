# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 7 — Adversarial phase: **COMPLETE**
- **Last completed checkpoint:** **P7** (2026-07-14) — all items pass; CI green on GitHub Actions for commit `873cf23`
- **Next action:** fresh session for **Phase 8 — Release readiness** (human-heavy). Context: `CLAUDE.md`, this file, `design/threat-model.md`, README, SECURITY.md; tasks: license decision executed (Apache-2.0 already chosen) + headers/NOTICE, finalize SECURITY.md, ROADMAP four-design narrative, blog #1 + Show HN drafts.

## Checkpoint P7 status

| Item | Status |
|---|---|
| ≥ 10 documented attack attempts in the threat model, each with outcome | ✅ `design/threat-model.md` logs **14** attacks (attack → vector → result → mitigation/accepted-risk) |
| Zero known critical findings open (critical = row data crosses the boundary) | ✅ One critical found (**CRIT-1**, comma-join label bypass) and **fixed**; no other row-data-crossing path remains after the pass |
| Threat model is publishable as-is — a feature, not an internal doc | ✅ Full doc: claim under test, assets/trust boundaries, attacker profiles, 14-row attack log, accepted residual risks, pass outcome. README section finalized and links to it |

Verification 2026-07-14: 153 tests green (unit + integration + adversarial; live-LLM/live-canary skip without a key), ruff clean.

## What Phase 7 found & did

- **CRIT-1 — comma-join label bypass (row data crossed).** `SELECT c.email FROM orders o, customers c …` hid the restricted `customers` table from `boundary/policy.py:_FROM_JOIN_RE` (it only captures the identifier right after FROM/JOIN), so the plan was labeled `internal` and allowed; the read-only role can read `customers`, so PII egressed. **Fixed** by failing closed on any implicit comma-join (`_COMMA_JOIN_RE` → `restricted`); explicit JOINs still resolve correctly, and the legitimate demo templates (select-list commas, `to_char` projections) are not over-restricted. The adapter shares `query_label`, so its defense-in-depth re-label inherited the fix.
- **Regression tests:** `tests/adversarial/test_boundary_attacks.py` (unit — label + policy) and `tests/adversarial/test_comma_join_live.py` (real DB — proves the RO role *could* read `customers`, the plan is denied end-to-end, the adapter labels it `restricted`, and errors don't leak param values).
- Everything else attacked held: DSL smuggling, template escapes, subquery/UNION label capture, aggregation-no-declassify, error sanitization, repair-loop-no-data, prompt injection bounded by the boundary, binary-search guards, audit tamper detection, guard-disable fail-closed.

## Notes for Phase 8

- **No frozen interface changed** in Phase 7; `INTERFACE_CHANGE_REQUEST` remains empty. CRIT-1 was a code fix.
- `design/threat-model.md` is publishable and is a launch asset (blog #1 highlight, Show HN credibility).
- License is Apache-2.0 (chosen in P0, recorded in DECISIONS.md) — Phase 8 applies headers + NOTICE and swaps the placeholder license comments now atop every source file.
- SECURITY.md is still a skeleton (disclosure contact + response expectation TODOs) — finalize in Phase 8.
- README residual-risk section and the threat model must be reviewed together in the Phase 8 final pass.

## Open questions (for the human)

_None._

## INTERFACE_CHANGE_REQUEST

_None._
