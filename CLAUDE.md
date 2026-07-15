# Fondaco — project conventions for Claude Code

**Fondaco** is a data boundary for AI over enterprise data. Tagline: *"Data stays home. Plans cross."* V1 is a reference architecture with a working implementation, not a maintained product. Scope is frozen by `IMPLEMENTATION_PLAN.md`; anything else goes to `ROADMAP.md`, never into code.

## Session start (every session)

Read ONLY: this file, `STATE.md`, and the design doc(s) listed for the current phase in `IMPLEMENTATION_PLAN.md`. Do not re-read the whole repo.

## Operating rules (from IMPLEMENTATION_PLAN.md §0 — not optional)

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
- Layout is fixed by `IMPLEMENTATION_PLAN.md` §2; do not add top-level directories.
- All artifacts in English (public-facing repo).
- License: Apache-2.0. Every source file carries an SPDX header (`# SPDX-License-Identifier: Apache-2.0` + copyright line); see `LICENSE` and `NOTICE`.
