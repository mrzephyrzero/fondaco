# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 6 — Packaging & demo polish: **COMPLETE**
- **Last completed checkpoint:** **P6** (2026-07-14) — all items pass; CI green on GitHub Actions for commit `e3faf62`
- **Next action:** fresh session for **Phase 7 — Adversarial phase** (separate sessions, adversarial system prompt). Context: `CLAUDE.md`, this file, code of `/boundary`, `/executor`, `/planner`; write findings into `design/threat-model.md`.

## Checkpoint P6 status

| Item | Status |
|---|---|
| Clean-machine test: fresh clone → working demo in ≤ 5 minutes, README only | ✅ Fresh `git clone` from GitHub (no `.env`, no local override), `docker compose up` → healthy in **35s**; scripted walkthrough (Q1 ask→approve→`internal` result, Q10 ask→**DENIED**/409, `/audit` hash chain verified) ran in ~1s. Total well under 5 min. Caveat: base images (`python:3.12-slim`, `postgres:16`) were already pulled locally — on a truly pristine machine add their one-time pull (~200 MB) |
| README explains the architecture in ≤ 1 screen before any install instructions | ✅ Rewritten: tagline → ASCII loop diagram + core claim (≤1 screen) → *then* Quick start. Demo banner, scripted walkthrough, real-planner profiles, gateway note, guards, "what this does NOT protect against", repo map |

Verification 2026-07-14: 138 tests green (unit + integration + adversarial; live-LLM/live-canary skip without a key), ruff clean. Demo scenarios run keyless in CI.

## What Phase 6 added

- **`planner/demo.py` — deterministic demo planner** (`FONDACO_PLANNER=demo`, the compose default): keyless, one hand-written validated plan per scripted question. Built via the new shared **`planner.client.assemble_plan`**, so a fixture plan is validated by the identical code an LLM plan is, then crosses the same policy/executor/guards/audit. `trace.prompt_version="demo-fixtures"` keeps the audit honest. Q10 reads restricted PII → policy-denied by design.
- **Transparency:** UI banner "DEMO MODE — no LLM involved" + README state it plainly; switching to a real planner is one env var (`FONDACO_PLANNER=llm`) + a profile.
- **README** rewritten architecture-first; **compose/.env.example** default to demo with cloud + local profiles documented; **`demo/scenarios.md`** notes the dual role.
- **Tests:** `tests/unit/test_demo_planner.py` (fixtures validate via the shared path, unknown → fail closed) and `tests/integration/test_demo_scenarios.py` (all 10 end-to-end through the real boundary, keyless, CI-friendly — also guards against dataset drift).

## Notes for Phase 7 (adversarial)

- Attack surface to hammer (from IMPLEMENTATION_PLAN.md §7): plan-DSL smuggling, SQL template escapes, label escalation via derived results, exfiltration via error messages, prompt injection from **hostile schema annotations** (column comments are planner-visible by design), canary extraction via the repair loop, binary-search aggregate exfiltration.
- Already-known residual risks to fold into `design/threat-model.md`: the aggregate/inference channel (guards raise cost, don't close it); no-auth cookie-reset budget; audit **tail-truncation** not detectable from the file alone (needs external head anchoring); policy label-scan reads any `FROM` conservatively → over-restricts (a feature, but worth an attacker's probe).
- The demo planner is a fixture path; Phase 7 should attack the **`llm` path** and the boundary itself, not the fixtures.
- README already carries a drafted "What this does NOT protect against" — Phase 7 finalizes it against the threat model.

## Open questions (for the human)

_None._

## INTERFACE_CHANGE_REQUEST

_None._
