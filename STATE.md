# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 5 — Guards: **COMPLETE**
- **Last completed checkpoint:** **P5** (2026-07-14) — all items pass; CI green on GitHub Actions for commit `329aba2` (adversarial suite runs in CI against the postgres service)
- **Next action:** fresh session for **Phase 6 — Packaging & demo polish**. Context: `CLAUDE.md`, this file, `demo/scenarios.md`, README.

## Checkpoint P5 status

| Item | Status |
|---|---|
| Attack simulation halted by budget; audit log flags the pattern | ✅ `tests/adversarial/test_binary_search_attack.py` (2 tests, real Postgres via API): every one-row `count` probe suppressed by k-threshold, run hard-stops at the query budget (429, DB never touched past the limit), `guard_decision` audit entries assert both guards fired and are visible at `/audit` |
| Guards documented in README "What this does NOT protect against" | ✅ drafted (aggregate channel raised-not-closed, no-auth/cookie budget reset, cross-grouping differencing, malicious approver, side channels) — finalized in Phase 7 |

Verification 2026-07-14: 111 tests green (92 unit + 17 integration + 2 adversarial), ruff clean; running container serves the budget UI and `guard_decision` audit filter.

## What Phase 5 added

`boundary/guards.py` — `suppress_small_groups` (k-threshold, drop not mask), `QueryBudget` (per-session hard stop, all-or-nothing), `config_from_env` (fail closed: k<1/negative/garbage → default, guard cannot be disabled). Wired: `executor/runner.py` suppresses every aggregate (new `RunResult.suppressed_groups`); `api/main.py` charges the budget before execution, emits new `guard_decision` audit event, sets a `fondaco_session` cookie; UI shows budget used/limit and suppression notices. `boundary/audit.py` gains `EVENT_GUARD_DECISION`.

## Canary re-run on the REAL external API path (2026-07-14) — **PASS**

The P3 canary claim had only ever been proven against a mocked transport. Re-run against the live cloud endpoint, after fixing the `temperature` rejection (see DECISIONS.md):

- **Endpoint:** `https://api.anthropic.com/v1`, `claude-sonnet-5` (credits funded by the human).
- **Planted:** 3 high-entropy sentinel tokens (e.g. `Z9QX7KWJ4PLUMBAT2VXQZZ8NROGGLE`) written as **row values** in `customers` — strings no model would regenerate on its own, so a hit could only mean the literal row crossed the wire. Verified readable from the DB first, so a leak was genuinely possible.
- **Inspected:** the **serialized outbound network payload** — the transport wraps a real `httpx.HTTPTransport` and records `request.content`, the exact bytes handed to the socket (not a pre-serialization dict). 1 request, 7 370 bytes.
- **Result:** planner returned a **valid plan on the first attempt** (`query,aggregate,present`); **0 of 3 canaries appeared in anything leaving the perimeter.**
- Durable, key-gated test: `tests/integration/test_canary_live.py` (skipped without `FONDACO_LLM_API_KEY`, so CI stays secret-free; the mocked `test_canary.py` remains the CI guard).

## Notes for Phase 6

- **Two planner profiles** to package and document: **cloud (default)** — Anthropic `claude-sonnet-5`, omits `temperature`; **local fallback** — host Ollama `qwen2.5-coder:7b`, pins `temperature=0`, `TIMEOUT_S=180`. Compose still defaults to the local profile so the demo runs keyless; `.env.example` documents both. Decide in Phase 6 which one `docker compose up` should pick by default.
- **Scope note (logged in DECISIONS.md):** the k-threshold guards *every* aggregate result, not just planner-facing ones — the plan text's "toward the planner in repair loops" scope would guard nothing here, since the repair loop feeds back only validation errors. The reader in the UI is the attacker.
- Demo tuning: default k=5 means a legitimate question whose grouping yields a <5-row group shows a suppression notice — worth surfacing in the walkthrough as a feature, and picking scenario questions whose groups are comfortably above k.
- README has "Guards" + "What this does NOT protect against"; Phase 6 adds the ≤1-screen architecture overview above install steps.
- Dev env: Python 3.12 (matches CI/Docker) — the previous 3.13 local venv gap is closed.

## Open questions (for the human)

_None._

## INTERFACE_CHANGE_REQUEST

_None._
