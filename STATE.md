# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 1 — Boundary core: **complete pending CI confirmation** (checkpoint below)
- **Last completed checkpoint:** **P1** (2026-07-13) — pending only the pushed CI run turning green (verified locally)
- **Next action:** fresh session for **Phase 2 — Executor + Postgres adapter**. Context: `CLAUDE.md`, this file, `design/adapter-contract.md`, `design/plan-dsl.md`.

## Checkpoint P1 status

| Item | Status |
|---|---|
| 100% of negative-case tests pass (≥ 15 hostile inputs) | ✅ 24 hostile cases in `tests/unit/test_validator_hostile.py`, all rejected with machine-readable codes; count asserted in-test |
| Audit log append-only by construction (tamper detection) | ✅ `boundary/audit.py` hash chain; tests detect edit / middle-deletion / reorder / garbage line; tampered log refuses further appends. Known limit (documented + in DECISIONS.md): tail truncation needs external head anchoring — Phase 7 item |
| Every error path in `/boundary` fails closed (grep-audit) | ✅ no bare `except`, no `except: pass`; sole `allow=True` sits after the label ≤ clearance check; validator/policy wrap all faults into invalid/deny |

Local verification 2026-07-13: `ruff check` + `ruff format --check` clean, `pytest -q` 51 passed.

## Notes for Phase 2

- `boundary/policy.py` labels query steps by whole-table over-approximation (see DECISIONS.md); the executor's per-step labeling in Phase 2 uses actual result columns via the adapter and may be tighter, but must never be lower than policy's static bound.
- Policy takes schema labels as a plain dict `{table: {"label": …, "columns": {col: …}}}`; adapt from `AnnotatedSchema` when the Postgres adapter lands.

## Open questions (for the human)

1. **Local Python** — dev machine has 3.13/3.11, not 3.12; CI and Docker pin 3.12 (frozen stack). Fine, or install 3.12 locally for exact parity?

## INTERFACE_CHANGE_REQUEST

_None._
