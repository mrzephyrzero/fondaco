# Implementation Plan — Data Boundary for AI over Enterprise Data (V1)

> **Audience:** Claude Code (agentic execution) + human architect (review & decisions).
> **Language:** English, because every artifact in this repo is public-facing.
> **Prime directive:** V1 is a *reference architecture with a working implementation*, not a maintained product. Scope is frozen. Anything not listed here goes to `ROADMAP.md`, never into code.

---

## 0. Operating rules for Claude Code (read before every session)

These rules exist to prevent context degradation and scope drift. They are not optional.

1. **One phase = one or more fresh sessions.** Never carry a session across phases. At session start, read ONLY: `CLAUDE.md`, `STATE.md`, and the design doc(s) listed for the current phase. Do not re-read the whole repo.
2. **`STATE.md` is the single source of progress truth.** At the end of every work block: update `STATE.md` (current phase, last completed checkpoint, open questions, next action), commit, stop. A new session must be able to resume from `STATE.md` alone.
3. **Checkpoints are gates, not suggestions.** A phase is complete only when every item in its "Checkpoint" list passes. If a checkpoint cannot pass, write the blocker in `STATE.md` and stop — do not work around it by widening scope.
4. **The three frozen interfaces may not be modified by Claude Code.** Plan DSL spec, label model, adapter contract live in `/design`. If implementation reveals a flaw, write the problem and a proposed change in `STATE.md` under `INTERFACE_CHANGE_REQUEST` and stop. Only the human approves interface changes (versioned, never silently edited).
5. **Boundary code is security code.** Every file under `/boundary` requires: parametrized queries only, fail-closed on every error path, no dynamic eval, input from the LLM treated as hostile. These files get adversarial review in Phase 7 — write them expecting attack.
6. **No new dependencies without justification.** Each new package must be recorded in `DECISIONS.md` with one line of rationale. Prefer stdlib.
7. **Tests accompany code in the same session**, not in a later "testing phase". Phase 7 is adversarial testing, not first testing.
8. **Commit style:** small commits, imperative messages, reference the phase (`P3: add plan executor happy path`).

---

## 1. Stack decisions (frozen)

| Decision | Choice | Rationale (one line) |
|---|---|---|
| Language | Python 3.12 | Ecosystem for data + author fluency; V1 boundary is not latency-bound |
| API framework | FastAPI | Standard, typed, async |
| Plan format | JSON conforming to `/design/plan-dsl.md` | Inspectable, diffable, schema-validatable |
| DB (first adapter) | PostgreSQL | Ubiquitous, demo-friendly; ERP adapters come later |
| LLM access | OpenAI-compatible client, base URL configurable | Works with frontier APIs, LiteLLM/Bifrost, Ollama unchanged |
| Frontend (approval UI) | Server-rendered HTML (Jinja) + htmx, no SPA | Minimum surface, no build chain |
| Packaging | Docker Compose (app + Postgres + demo data) | `docker compose up` must be the entire install story |
| License | To be chosen by human before Phase 8 (Apache-2.0 vs AGPL-3.0) | Open-core strategy decision, not a coding decision |

---

## 2. Repository layout (created in Phase 0)

```
/design/                  # Frozen interfaces — human-owned
  plan-dsl.md             # Plan DSL specification v0
  label-model.md          # Data classification & propagation model v0
  adapter-contract.md     # Adapter interface v0
  threat-model.md         # Written in Phase 7, skeleton from Phase 0
/boundary/                # Security-critical core (small, reviewed)
  validator.py            # Plan validation against DSL schema + policy
  policy.py               # Label/egress policy engine
  guards.py               # Cardinality thresholds, query budget
  audit.py                # Append-only audit log
/planner/
  client.py               # LLM client (schema+question in, plan out)
  prompts/                # Versioned prompt templates
/executor/
  runner.py               # Executes validated plans, deterministic
  adapters/
    postgres.py           # First adapter, implements adapter-contract
/api/
  main.py                 # FastAPI app: ask → plan → approve → execute
  ui/                     # Approval + audit views
/demo/
  dataset/                # Synthetic warehouse-flavored dataset + loader
  scenarios.md            # 10 scripted demo questions
/tests/
  unit/  integration/  adversarial/
CLAUDE.md  STATE.md  DECISIONS.md  ROADMAP.md  SECURITY.md  README.md
```

---

## 3. Phases

### Phase 0 — Foundations (human-heavy; Claude Code assists, does not decide)

**Objective:** freeze the three interfaces and scaffold the repo so every later session has stable ground.

Tasks:
- Write `/design/plan-dsl.md` v0: plan = ordered list of steps; step types limited to `query` (read-only SQL template + typed params), `aggregate`, `present`. Explicitly listed non-goals: writes, DDL, loops, external calls.
- Write `/design/label-model.md` v0: classification levels (e.g., `public / internal / confidential / restricted`), how labels attach to schema objects (table/column annotations), max-label propagation rule for derived results, egress rule (result label ≤ endpoint clearance).
- Write `/design/adapter-contract.md` v0: `get_schema() -> AnnotatedSchema`, `execute(step) -> LabeledResult`, `capabilities()`. Read-only by contract.
- Scaffold repo layout, `CLAUDE.md` (project conventions + the operating rules above), empty `STATE.md`, `DECISIONS.md`, `ROADMAP.md`, `SECURITY.md` (disclosure contact + supported-scope statement).
- CI: lint + tests on push (GitHub Actions).

**Checkpoint P0:**
- [ ] Three design docs exist, versioned `v0`, marked FROZEN with a change-request procedure.
- [ ] `docker compose up` starts an empty app + Postgres (health endpoint answers).
- [ ] CI green on the skeleton.
- [ ] Human sign-off recorded in `DECISIONS.md`.

**Time gate:** interface design gets a maximum of 2 weekends of discussion. At the deadline, freeze v0 as-is — interfaces are versioned later, never perfected first.

---

### Phase 1 — Boundary core: validator, policy, audit

**Objective:** the security spine, built before anything that uses it.

Context to load: `plan-dsl.md`, `label-model.md`.

Tasks:
- `boundary/validator.py`: JSON-schema validation of plans + structural rules (only whitelisted step types, only SELECT templates, parametrized params, no string interpolation anywhere).
- `boundary/policy.py`: evaluate a validated plan against schema labels → allow / deny with machine-readable reason.
- `boundary/audit.py`: append-only log (JSONL + hash chain of entries) recording: question, generated plan, validation result, policy decision, approval identity, execution digest. Nothing that crosses the boundary is unlogged.
- Unit tests including *negative* cases: malformed plans, injection attempts in params, unknown step types, label escalation attempts.

**Checkpoint P1:**
- [ ] 100% of negative-case tests pass (list of at least 15 hostile inputs).
- [ ] Audit log is append-only by construction (test proves tamper detection via hash chain).
- [ ] Every error path in `/boundary` fails closed (grep-audit: no bare `except: pass`, no default-allow).

---

### Phase 2 — Executor + Postgres adapter

**Objective:** deterministic execution of validated plans against real data.

Context to load: `adapter-contract.md`, `plan-dsl.md`.

Tasks:
- `executor/runner.py`: executes plans step-by-step; every step result carries the max label of its inputs.
- `executor/adapters/postgres.py`: implements the contract; read-only DB role enforced at connection level (defense in depth, not only in code).
- `/demo/dataset`: synthetic warehouse-flavored dataset (orders, deliveries, stock movements, ~50k rows) with labeled schema annotations, loaded by compose.
- Integration tests: plans run against demo data, results labeled correctly.

**Checkpoint P2:**
- [ ] A hand-written plan (no LLM yet) runs end-to-end and returns labeled results.
- [ ] DB user provably cannot write (test attempts INSERT/UPDATE/DDL and fails at the DB layer).
- [ ] Label propagation verified on a multi-step plan.

---### Phase 3 — Planner: LLM generates plans from schema only

**Objective:** the frontier model as blind planner.

Context to load: `plan-dsl.md`, `planner/prompts/`.

Tasks:
- `planner/client.py`: sends *only* annotated schema + statistics + user question; receives a plan; never row data. Base URL configurable (frontier API, gateway, or local model — same code path).
- Prompt template versioned in `/planner/prompts` with a changelog.
- Retry/repair loop: if the plan fails validation, the *validation error* (not data) goes back to the model, max N attempts, then fail closed.
- Network assertion test: instrument the LLM client and prove that no request payload ever contains strings from data rows (canary values planted in demo data must never appear in outbound traffic).

**Checkpoint P3:**
- [ ] 8 of the 10 demo questions in `scenarios.md` produce a valid, policy-passing plan within 2 attempts.
- [ ] Canary test green: planted row values never leave the perimeter.
- [ ] Swapping base URL to a local Ollama model requires zero code changes (documented smoke test).

---

### Phase 4 — API + approval flow + audit UI

**Objective:** the human-visible loop: ask → inspect plan → approve → results.

Context to load: `plan-dsl.md` (rendering), Phase 1 audit format.

Tasks:
- Endpoints: `POST /ask`, `GET /plans/{id}`, `POST /plans/{id}/approve|reject`, `GET /audit`.
- Approval UI: render the plan human-readably (each step: what it reads, its label, why policy allowed it). Approve/reject buttons. No auto-execute in V1 — manual approval is the demo, not a limitation.
- Audit UI: filterable log view; every entry links question → plan → decision → result digest.

**Checkpoint P4:**
- [ ] Full loop demo-able in under 2 minutes from `docker compose up`.
- [ ] A rejected plan is provably never executed (test).
- [ ] Audit view shows the complete chain for every request ever made.

---

### Phase 5 — Guards: cardinality thresholds and query budgets

**Objective:** honest mitigation of the aggregate-exfiltration channel.

Context to load: `label-model.md`, Phase 1 policy engine.

Tasks:
- `boundary/guards.py`: k-threshold on any aggregate returned toward the planner in repair/iteration loops (result suppressed below cardinality k, configurable); per-session query budget with hard stop.
- Config surface documented: `k`, budget size, fail behavior (always closed).
- Tests: binary-search attack simulation script under `/tests/adversarial` demonstrating that the budget halts the attack and the audit log makes it visible.

**Checkpoint P5:**
- [ ] Attack simulation halted by budget; audit log flags the pattern.
- [ ] Guards documented in README section "What this does NOT protect against" (drafted here, finalized in Phase 7).

---

### Phase 6 — Packaging & demo polish

**Objective:** the five-minute stranger experience.

Tasks:
- `docker compose up` → seeded data → UI at localhost → scripted walkthrough in README (with the 10 scenario questions).
- Config file with commented defaults; `.env.example`.
- Optional adapter stub: LiteLLM/Bifrost as planner base URL, documented in one page ("works behind your existing gateway").

**Checkpoint P6:**
- [ ] Clean-machine test: fresh clone to working demo in ≤ 5 minutes, following only the README.
- [ ] README explains the architecture in ≤ 1 screen before any install instructions.

---

### Phase 7 — Adversarial phase (separate sessions, adversarial system prompt)

**Objective:** attack the boundary; convert findings into fixes and content.

Rules: run in *fresh sessions* whose only goal is breaking the system. Load the code of `/boundary`, `/executor`, `/planner` and try: plan-DSL smuggling, SQL template escapes, label-escalation via derived results, exfiltration via error messages, prompt-injection from schema annotations (hostile column comments!), canary extraction via repair loop.

Tasks:
- Log every finding in `/design/threat-model.md`: attack, result, mitigation or accepted-risk statement.
- Fix what is fixable within frozen interfaces; file the rest as `INTERFACE_CHANGE_REQUEST` or documented residual risk.
- Finalize README's "What this does NOT protect against".

**Checkpoint P7:**
- [ ] ≥ 10 documented attack attempts in the threat model, each with outcome.
- [ ] Zero known critical findings open (critical = row data crosses the boundary).
- [ ] Threat model is publishable as-is — it is a feature, not an internal doc.

---

### Phase 8 — Release readiness (human-heavy)

Tasks:
- License decision executed (see open decisions below); headers + NOTICE.
- `SECURITY.md` finalized (disclosure channel, response expectation honest for a solo maintainer, supported scope: "reference architecture" framing).
- `ROADMAP.md`: the four-design narrative — labels-at-source deepening (D3), abstraction mode (D2), attestable backends (D4), ERP adapter — declared as direction, not promises.
- Blog post #1 draft (why + architecture + threat model highlights) and Show HN text draft.

**Checkpoint P8 (= launch gate):**
- [ ] Clean-machine test repeated by a human who is not the author.
- [ ] Threat model, README, SECURITY.md reviewed in one final pass.
- [ ] `STATE.md` archived; repo history clean.

---

## 4. Cross-phase definition of "context hygiene" (summary card)

- New phase → new session. Mid-phase resume → read `STATE.md` first, nothing else beyond the phase's listed docs.
- Never paste whole files into discussion when a path reference suffices.
- When output quality degrades (repetition, forgotten constraints): stop, update `STATE.md`, restart session.
- Design docs are read-only context; code is working context; conversation history is disposable.
