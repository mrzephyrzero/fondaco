# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 3 — Planner: **complete pending CI confirmation** (checkpoint below)
- **Last completed checkpoint:** **P3** (2026-07-14) — pending only the pushed CI run turning green (checkpoint items verified locally)
- **Next action:** fresh session for **Phase 4 — API + approval flow + audit UI**. Context: `CLAUDE.md`, this file, `design/plan-dsl.md` (rendering), Phase 1 audit format (`boundary/audit.py`).

## Checkpoint P3 status

| Item | Status |
|---|---|
| 8/10 scenario questions → valid, policy-passing plan within 2 attempts | ✅ 8/9 answerable pass **and execute end-to-end** (stricter than required); Q10 policy-denied by design; Q9 (monthly grouping) failed — within budget. Run on **Ollama qwen2.5-coder:7b**, prompt v3, clearance `internal` |
| Canary test green: planted row values never leave the perimeter | ✅ `tests/integration/test_canary.py`: canaries inserted as rows; full ask-flow incl. forced repair round; all outbound LLM request bodies captured and clean |
| Base URL swap to local Ollama = zero code changes | ✅ demonstrated beyond doubt: the entire checkpoint run used Ollama via `FONDACO_LLM_BASE_URL` — no code changes. Documented in README "Planner configuration" |

Scenario outcomes (2026-07-14, qwen2.5-coder:7b, prompt v3): Q1–Q8 ok (Q5 `public`, rest `internal`), Q9 `attempts_exhausted`, Q10 `policy_deny:label_exceeds_clearance` (expected).

## Notes for Phase 4 (and the human)

- **Anthropic endpoint (the human's chosen default) is currently blocked: API credit balance too low** (HTTP 400 from `api.anthropic.com`; auth itself works). When topped up, re-run `pytest tests/integration/test_scenarios_llm.py` with `FONDACO_LLM_API_KEY` set — expected to beat the 7B model's 8/9.
- Prompt lesson (v3 changelog): the policy label-scan reads any `FROM` as a table reference, so `EXTRACT(YEAR FROM col)` → `restricted` deny. Policy engine deliberately left untouched (fail closed, operating rule 5); prompt teaches half-open date ranges instead. Phase 7 should attack this scan.
- Q8 passed but returned 0 rows (model filtered `status = 'canceled'`, data says `cancelled`) — plan quality, not boundary concern; consider a column-comment hint in the demo schema (Phase 6 polish).
- `PlanningTrace` (attempt history) is returned by `PlannerClient.generate_plan` and awaits wiring into `boundary.audit` in Phase 4.
- Local inference needs `FONDACO_LLM_TIMEOUT_S=180`.

## Open questions (for the human)

1. **Anthropic credits** — top up to run the checkpoint on the chosen frontier endpoint (current pass used local Ollama; checkpoint formally satisfied either way).
2. **Local Python** — dev machine has 3.13/3.11, not 3.12; CI and Docker pin 3.12. Fine, or install 3.12 for parity?

## INTERFACE_CHANGE_REQUEST

_None._
