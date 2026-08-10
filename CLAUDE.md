# Fondaco — project conventions for Claude Code

**Fondaco** is a data boundary for AI over enterprise data. Tagline: *"Data stays home. Plans cross."* V1 is a reference architecture with a working implementation, not a maintained product. **V1 scope is frozen**; anything outside it goes to `ROADMAP.md`, never into code.

## Session start (every session)

Read ONLY: this file, `STATE.md`, and the design doc(s) relevant to the work at hand. Do not re-read the whole repo.

## Operating rules (not optional)

1. **One phase = one or more fresh sessions.** Never carry a session across phases.
2. **`STATE.md` is the single source of progress truth.** At the end of every work block: update it (current phase, last completed checkpoint, open questions, next action), commit, stop. A new session must be able to resume from `STATE.md` alone.
3. **Checkpoints are gates.** A phase is complete only when every checkpoint item passes. If one cannot pass, write the blocker in `STATE.md` and stop — never widen scope to work around it.
4. **The three frozen interfaces may not be modified by Claude Code**: `design/plan-dsl.md`, `design/label-model.md`, `design/adapter-contract.md`. If implementation reveals a flaw, file it in `STATE.md` under `INTERFACE_CHANGE_REQUEST` and stop. Only the human approves interface changes — versioned, never silently edited.
5. **Boundary code is security code.** Everything under `/boundary`: parametrized queries only, fail closed on every error path, no dynamic eval, LLM input treated as hostile. Written expecting attack (adversarial review in Phase 7).
6. **No new dependencies without justification.** Every package gets one line of rationale in `DECISIONS.md`. Prefer stdlib.
7. **Tests accompany code in the same session** — Phase 7 is adversarial testing, not first testing.
8. **Commit style:** small commits, imperative messages, phase-prefixed (`P3: add plan executor happy path`).

## Context hygiene

New phase → new session. Mid-phase resume → read `STATE.md` first. Never paste whole files where a path reference suffices. If output quality degrades: stop, update `STATE.md`, restart the session. Design docs are read-only context; code is working context; conversation history is disposable.

## Conventions

- Python 3.12, FastAPI, PostgreSQL, Docker Compose (`docker compose up` is the entire install story). Frontend: Jinja + htmx, no SPA, no build chain.
- Lint: `ruff check .` · Format: `ruff format` · Tests: `pytest` — CI runs lint + tests on every push.
- **The layout below is fixed. Do not add top-level directories.**
- All artifacts in English (public-facing repo).
- License: Apache-2.0. Every source file carries an SPDX header (`# SPDX-License-Identifier: Apache-2.0` + copyright line); see `LICENSE` and `NOTICE`.

## Repository layout (fixed)

```
/design/                  # Interfaces — human-owned
  plan-dsl.md             # Plan DSL specification v0 (FROZEN)
  label-model.md          # Data classification & propagation model v1 (FROZEN)
  adapter-contract.md     # Adapter interface v0 (FROZEN)
  threat-model.md         # Adversarial pass: 14 attacks and their outcomes
  prior-art.md            # Positioning against existing work
/boundary/                # Security-critical core (small, reviewed)
  validator.py            # Plan validation against DSL schema + structural rules
  policy.py               # Label/egress policy engine
  guards.py               # Cardinality threshold, per-session query budget
  audit.py                # Append-only audit log
/planner/
  client.py               # LLM client (schema+question in, plan out)
  demo.py                 # Deterministic fixture planner, no LLM
  prompts/                # Versioned prompt templates
/executor/
  runner.py               # Executes validated plans, deterministic
  adapters/
    contract.py           # Types the adapter contract is written against
    postgres.py           # First adapter
/api/
  main.py                 # FastAPI app: ask → plan → approve → execute
  ui/                     # Approval + audit views (Jinja + htmx)
/demo/
  dataset/                # Synthetic warehouse-flavored dataset + loader
  scenarios.md            # 10 scripted demo questions
/tests/
  unit/  integration/  adversarial/
CLAUDE.md  STATE.md  DECISIONS.md  ROADMAP.md  SECURITY.md  README.md
LICENSE  NOTICE
```

**Published vs local.** Everything above is published. The artifacts that served
the *construction* of the project rather than a reader of it stay on disk and are
gitignored: `IMPLEMENTATION_PLAN.md` (the build plan, spent), `STATE.local.md`
(the session journal), `LAUNCH_DRAFTS.md`. The published `STATE.md` is the
`INTERFACE_CHANGE_REQUEST` register and a status statement — nothing else. When
an operating rule below says to write something in `STATE.md`, that means the
local journal, except for interface change requests, which are public.
