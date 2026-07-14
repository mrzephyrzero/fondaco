# Fondaco

> **Data stays home. Plans cross.**

Fondaco is a **data boundary for AI over enterprise data**. A frontier LLM is
used as a *blind planner*: it sees only your annotated schema and the user's
question — **never a single row of data** — and returns an inspectable JSON
*plan*. That plan is validated, checked against a classification-label policy,
approved by a human, and only then executed deterministically inside your
perimeter against a read-only database role. Every crossing is written to an
append-only, hash-chained audit log.

```
  question ─▶ Planner (LLM)        sees: schema + question, never rows
                  │  plan (JSON)
                  ▼
            ┌───────────── the boundary (your perimeter) ─────────────┐
            │  Validator   plan matches the DSL, SELECT-only, params   │
            │  Policy      max-label ≤ endpoint clearance, else deny   │
            │  Approval    a human approves; deny is never overridable │
            │  Executor    runs the plan on a read-only DB role        │
            │  Guards      k-threshold + per-session query budget      │
            │  Audit       append-only, hash-chained, nothing skipped  │
            └─────────────────────────────────────────────────────────┘
                  │  labeled result
                  ▼
             approver sees the answer
```

**Why it holds:** the model that could be tricked into leaking data never
touches data; the code that touches data is small, deterministic, and can't be
prompted. Reads are enforced read-only at the database itself, not just in
code. This is a **reference architecture with a working implementation**, not a
maintained product — see `IMPLEMENTATION_PLAN.md` and `STATE.md`.

---

## Quick start (≈5 minutes, no API key)

Requires only Docker.

```sh
git clone <this-repo> && cd fondaco
docker compose up
```

Open **http://localhost:8000**. The stack seeds a synthetic warehouse dataset
(~51k rows) and starts in **demo mode**: plans are pre-generated fixtures for
the scripted questions below — **no LLM is involved** — but they cross the exact
same validator, policy, executor, guards, and audit as a real request. A banner
in the UI says so. To use a real planner, see [Using a real planner](#using-a-real-planner).

### Scripted walkthrough

On the home page, click one of the scripted questions (or type it) and press
**Generate plan**. Try, in order:

1. **"How many orders were placed per region since October 2025?"** — inspect
   the plan: each step shows the SQL it runs, its typed parameters, and its
   classification **label**. The policy line reads *ALLOWED — result label
   internal ≤ clearance internal*. Click **Approve & execute** → a labeled
   result table, with a result digest.
2. **"How many products do we have per category?"** — same loop, but the result
   is labeled **public** (the `products` table is public).
3. **"List the names and emails of customers in Venezia."** — this one is
   **DENIED**: it reads `customers`, which is `restricted` PII, above the
   `internal` clearance. There is no approve button — *approval cannot override
   a policy deny.* This is the boundary doing its job.
4. Open **Audit log** (top nav). Every request has a complete, hash-chained
   trail: question → plan → validation → policy decision → approval → execution
   digest, with a "✓ hash chain verified" banner. Filter by event or plan id.

That is the whole product: **ask → inspect the plan → approve → labeled result**,
with nothing crossing the boundary unlogged.

---

## Using a real planner

The planner talks to any OpenAI-compatible `/chat/completions` endpoint; the
base URL is configuration, not code. Set `FONDACO_PLANNER=llm` and pick a
profile (full list in `.env.example`):

**Cloud** (default profile):

```sh
FONDACO_PLANNER=llm
FONDACO_LLM_BASE_URL=https://api.anthropic.com/v1
FONDACO_LLM_MODEL=claude-sonnet-5
FONDACO_LLM_API_KEY=sk-...
```

**Local, no key** (Ollama on the docker host):

```sh
FONDACO_PLANNER=llm
FONDACO_LLM_BASE_URL=http://host.docker.internal:11434/v1
FONDACO_LLM_MODEL=qwen2.5-coder:7b
FONDACO_LLM_TEMPERATURE=0
```

`ollama pull qwen2.5-coder:7b` first (~4.7 GB). With a real planner you can ask
free-form questions; demo mode only answers the scripted ten.

### Behind your existing gateway

LiteLLM, Bifrost, or any OpenAI-compatible proxy work with **zero code
changes** — point `FONDACO_LLM_BASE_URL` at the gateway and set the model it
exposes. Fondaco never uses a vendor SDK; it speaks plain
`/chat/completions`, so whatever your gateway fronts (frontier APIs, self-hosted
models) is reachable through the same code path.

---

## Guards (aggregate-exfiltration mitigation)

Even when every individual plan is valid and policy-approved, a *sequence* of
aggregate questions can binary-search a single row's value. Two guards
(`boundary/guards.py`) raise the cost and make it visible — they do not close
the channel:

- **k-threshold** (`FONDACO_GUARD_K`, default 5): any aggregate group computed
  from fewer than k input rows is dropped entirely (not masked). This removes
  the one-row `count` that a binary search depends on.
- **Per-session query budget** (`FONDACO_QUERY_BUDGET`, default 20): a hard stop
  on executed query steps; over budget, the plan is refused, unrun, recorded.

Both fail closed: a config value below 1 or unparsable reverts to the default —
a guard cannot be disabled.

## What this does NOT protect against

_Draft, finalized in Phase 7's threat model. Fondaco is a reference
architecture; this list is deliberately honest._

- **Aggregate/inference channel — raised in cost, not closed.** The k-threshold
  and query budget slow statistical inference and make it auditable, but a
  patient attacker within budget, or across sessions, can still learn
  distributional facts. Fondaco protects against *row data crossing the
  boundary*, not against all inference over aggregates.
- **No authentication in V1.** The approver identity is self-declared and the
  query budget is keyed on a session cookie, so clearing cookies grants a fresh
  budget. The mitigation is the append-only audit trail, not prevention.
  Per-user identity/authz is a deployment concern (roadmap).
- **Differencing across groupings.** The k-threshold suppresses small groups
  within one result but does not defend against combining two different legal
  aggregates to isolate an individual.
- **A malicious approver.** Approval is the trust anchor; someone who approves
  an exfiltrating plan is out of scope by design.
- **Side channels** (timing, error text, query duration) beyond those already
  sanitized are examined in Phase 7.

---

## Repository map

| Path | What lives here |
|---|---|
| `design/` | Frozen interfaces (plan DSL, label model, adapter contract) + threat model |
| `boundary/` | Security core: `validator`, `policy`, `guards`, `audit` |
| `planner/` | Blind LLM planner (`client`) + the demo fixture planner (`demo`) + prompts |
| `executor/` | Deterministic plan runner + `adapters/` (Postgres) |
| `api/` | FastAPI app + Jinja/htmx approval & audit UI |
| `demo/` | Synthetic dataset loader + the 10 scripted `scenarios.md` |
| `tests/` | `unit/`, `integration/`, `adversarial/` |

Config surface is documented in `.env.example`.
