# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 2 — Executor + Postgres adapter: **COMPLETE**
- **Last completed checkpoint:** **P2** (2026-07-14) — all items pass; CI green on GitHub Actions for commit `1f94edf` (including integration tests against the postgres service container)
- **Next action:** fresh session for **Phase 3 — Planner (LLM generates plans from schema only)**. Context: `CLAUDE.md`, this file, `design/plan-dsl.md`, `planner/prompts/`.

## Checkpoint P2 status

| Item | Status |
|---|---|
| Hand-written plan (no LLM) runs end-to-end with labeled results | ✅ `tests/integration/test_end_to_end_plan.py`: query→aggregate→present over 20k orders returns per-region counts+revenue, label `internal` |
| DB user provably cannot write (fails at the DB layer) | ✅ `tests/integration/test_readonly_role.py`: INSERT/UPDATE/DELETE/CREATE/DROP/ALTER as `fondaco_ro` all denied with SQLSTATE 42501/25006 |
| Label propagation verified on a multi-step plan | ✅ restricted `customers` → aggregate → present stays `restricted` (integration) + unit propagation tests with fake adapter |

Verification 2026-07-14: 73 tests green (59 unit + 14 integration), ruff clean, `docker compose up` seeds ~51k rows and app connects as the read-only role (`/health` db:ok).

## Notes for Phase 3

- Adapter surface for the planner: `PostgresAdapter.get_schema()` returns `AnnotatedSchema` (labels + row counts, never sample values); `executor/adapters/contract.py:schema_labels_dict` bridges to `boundary.policy.evaluate`.
- Two findings fed back into code this phase: Postgres `numeric` → `Decimal` in aggregates; e2e demo plans must stay under adapter `max_rows` (10 000 raw rows) — scenario questions in `demo/scenarios.md` should aggregate early or filter tightly.
- Windows dev quirk: use `127.0.0.1`, not `localhost`, in local test DSNs (IPv6 stall: 18 min vs 0.7 s suite time).

## Open questions (for the human)

1. **Local Python** — dev machine has 3.13/3.11, not 3.12; CI and Docker pin 3.12 (frozen stack). Fine, or install 3.12 locally for exact parity?

## INTERFACE_CHANGE_REQUEST

_None._
