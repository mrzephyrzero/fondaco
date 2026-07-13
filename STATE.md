# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 0 — Foundations
- **Last completed checkpoint:** none (P0 built, awaiting human gate — see below)
- **Next action:** human reviews the three draft design docs in `/design`, freezes them as v0 (edit status line DRAFT → FROZEN), and records sign-off in `DECISIONS.md`. Then: fresh session for Phase 1 (context: `CLAUDE.md`, this file, `design/plan-dsl.md`, `design/label-model.md`).

## Checkpoint P0 status

| Item | Status |
|---|---|
| Three design docs exist, versioned v0, with change-request procedure | ✅ written — but marked **DRAFT — pending human freeze**, not yet FROZEN (human gate) |
| `docker compose up` starts app + Postgres; health endpoint answers | ✅ verified 2026-07-13: both containers healthy, `/health` → `{"status":"ok","db":"ok"}` |
| CI green on the skeleton | ✅ locally: `ruff check`, `ruff format --check`, `pytest` all pass (Python 3.13 venv). ⚠️ GitHub Actions run itself pending first push to a GitHub remote (none configured yet) |
| Human sign-off recorded in `DECISIONS.md` | ⬜ pending (placeholder row exists) |

## Open questions (for the human)

1. **Freeze review** — decisions I made in the drafts that the plan left open (details in each doc): 4 fixed label levels with names `public/internal/confidential/restricted`; unlabeled = `restricted`; aggregation does not declassify; max 10 steps/plan; exactly one `present` as final step; aggregate ops limited to count/sum/avg/min/max; param types string/int/float/bool/date/timestamp; `%(name)s` placeholder syntax; adapter `max_rows` 10 000 / timeout 30 s; sanitized `AdapterError` taxonomy; approver cannot override policy deny.
2. **GitHub remote** — create repo and push so the CI checkpoint item can turn green in Actions (workflow is ready at `.github/workflows/ci.yml`).
3. **Local Python** — dev machine has 3.13/3.11, not 3.12; CI and Docker pin 3.12 (frozen stack). Fine, or install 3.12 locally for exact parity?

## INTERFACE_CHANGE_REQUEST

_None. (Interfaces are still drafts; this section becomes active after freeze.)_
