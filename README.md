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
            │  Budget      per-session query budget, charged up front  │
            │  Executor    runs the plan on a read-only DB role        │
            │   └ k-threshold  small aggregate groups dropped, in-run  │
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
maintained product: V1 is complete and the threat model is closed, but there are
no releases, no backports, and no support commitment.

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
   trail: question → plan id → validation → policy decision → approval →
   execution digest, with a "✓ hash chain verified" banner. Filter by event or
   plan id. Note what the trail does *not* carry: the plan's steps and SQL are
   never written to the log, only its `plan_id`. Plan content lives in memory
   for the life of the process, so after a restart the audit shows that a plan
   ran and what was decided about it, not what it executed.

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
  the one-row `count` that a binary search depends on. It does *not* touch
  `min`/`max`, which return one row's exact value at any group size — what
  bounds those is the label, not this guard.
- **Per-session query budget** (`FONDACO_QUERY_BUDGET`, default 20): a hard stop
  on executed query steps; over budget, the plan is refused, unrun, recorded.

Unparsable config, or a value below 1, reverts to the default rather than to no
protection. That is the limit of the claim: `FONDACO_GUARD_K=1` is accepted and
suppresses nothing, so the k-threshold *can* be turned off deliberately — it
cannot be turned off by accident or by a malformed value.

## What this does NOT protect against

Fondaco is a reference architecture; this list is deliberately honest. The full
attack log — 14 documented attacks, each with its outcome, including the ones
it does not stop — is in
[`design/threat-model.md`](design/threat-model.md). That pass found and fixed
one critical (a comma-join that hid a restricted table from the label scanner,
letting PII cross); it is closed and has a regression test. The threat model
ships *because* it shows real bugs caught, not despite it. Known residual risks:

- **Aggregate/inference channel — raised in cost, not closed.** The k-threshold
  and query budget slow statistical inference and make it auditable, but a
  patient attacker within budget, or across sessions, can still learn
  distributional facts. Fondaco protects against *row data crossing the
  boundary*, not against all inference over aggregates.
- **No authentication in V1.** The approver identity is self-declared, and the
  query budget is keyed on a session cookie the client chooses to send. A
  browser that clears cookies gets a fresh budget; a client that never sends one
  gets a fresh budget on *every request*, so against non-browser callers the
  budget is not a limit at all. The mitigation is the append-only audit trail,
  not prevention. Per-user identity/authz is a deployment concern (roadmap).
- **Differencing across groupings.** The k-threshold suppresses small groups
  within one result but does not defend against combining two different legal
  aggregates to isolate an individual.
- **A malicious approver.** Approval is the trust anchor; someone who approves
  an exfiltrating plan is out of scope by design.
- **The two label checks are not independent.** The static pre-execution check
  and the label the adapter recomputes both call the same `query_label`. A flaw
  in that one function defeats both at once — which is exactly how the
  comma-join critical got through. Two checks, one point of failure.
- **Views are denied by ignorance, not resolved.** A view is never in the
  schema labels (only ordinary tables are read), so a query against one is
  denied because the *name* is unknown — not because anything traced the view
  to its base tables. Every view is unusable, including views over public data.
  If views were ever added to the schema labels to fix that, the label on the
  view would be believed: a view labeled `internal` over a `restricted` table
  would leak. The protection does not weaken, it inverts. The same blindness
  covers everything that is not an ordinary table: **partitioned tables**,
  materialized views and foreign tables are all invisible to the schema reader
  and therefore unusable — which rules out the normal shape of a large table.
- **`min` and `max` disclose one row exactly.** They return a real cell value by
  construction, whatever the group size, so the k-threshold never applies to
  them. Only the label bounds that disclosure: the value crosses if its label is
  within clearance. Aggregation is not anonymization here.
- **The hash chain is unkeyed.** Plain SHA-256, algorithm in this repository.
  It detects edits, deletions, and reordering *within* the log. It does not
  detect tail-truncation, deletion of the whole file, or a wholesale rewrite
  with recomputed hashes — that forgery verifies clean. Tamper-evident against
  partial edits, not tamper-proof against write access plus knowledge of the
  algorithm.
- **The audit does not contain plan content.** Only `plan_id`, `prompt_version`
  and `attempts` are logged. The steps and SQL live in process memory, so after
  a restart you cannot reconstruct what was executed — only that it was.
- **k=5 suppresses legitimate small groups.** The threshold is on input
  cardinality and cannot tell an exfiltration probe from a real answer: both are
  1-row groups. On operational data, where a 1–3 row group is the signal rather
  than the risk, the numbers you asked for are exactly the ones dropped. The
  only lever is `FONDACO_GUARD_K=1`, which disables the binary-search defence.
  There is no per-query way to keep the guard and answer the question.
- **Row counts shown to the planner are estimates.** They come from Postgres
  `reltuples`, refreshed by `ANALYZE`/autovacuum, and the model is given no hint
  that the number is approximate. A plan can be built on a stale count.
- **Side channels** (timing, query duration) beyond error text, which is already
  sanitized to exception class + SQLSTATE.

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
