# Launch drafts

Drafts for human review before publishing. Not part of the product; not
referenced by code. Edit freely.

---

## Blog post #1 (draft) — "Data stays home. Plans cross."

### The problem

Every enterprise wants to point a frontier model at its data. Almost none can
let the data leave. The moment rows flow to a model API — or even to a
self-hosted model outside the trust boundary — you've created a copy you can't
label, can't audit, and can't take back. So the interesting question isn't
"which model is smartest," it's: **can the model be useful over data it is
never allowed to see?**

Fondaco is a reference architecture that answers yes, by moving the boundary.

### The idea: a blind planner

Instead of sending data to the model, send the model the *question* and the
*shape of the data* — annotated schema, classification labels, row counts — and
ask it for a **plan**, not an answer. The plan is a small, inspectable JSON
document in a locked-down DSL: read-only SQL templates with typed parameters,
in-boundary aggregation, and a final "present" step. The model never sees a
single row.

That plan then crosses into your perimeter, where deterministic, un-promptable
code takes over:

- **Validate** — the plan must match the DSL schema and structural rules:
  SELECT-only, one statement, parameterized, no comments, no DDL. Anything off
  the whitelist is rejected.
- **Label & gate** — every table the plan reads carries a classification label;
  the result's label is the max over what it touches; egress is allowed only if
  that label is within the endpoint's clearance. Otherwise: deny.
- **Approve** — a human sees the plan, the per-step labels, and the policy
  verdict, and clicks approve. A policy *deny* can't be overridden by approval.
- **Execute** — the plan runs against a database role that is read-only *at the
  database*, not just in code.
- **Guard & audit** — cardinality thresholds and a query budget blunt
  aggregate-inference attacks; every crossing lands in an append-only,
  hash-chained log.

The model that could be tricked into leaking data never touches data. The code
that touches data is small, deterministic, and can't be talked into anything.

### Why the threat model is the feature

A security architecture is only as honest as its "what it doesn't protect
against" section. Fondaco ships a threat model with **14 documented attacks**,
each with its outcome — and it includes a real bug the adversarial pass found
and fixed: a comma-join (`FROM orders o, customers c`) that hid a restricted
table from the label scanner, so a plan reading customer PII got labeled as if
it only read orders, and would have been allowed to cross. It's closed now,
with a regression test — but the point is that the document shows the seams, not
just the guarantees. Known residual risks (aggregate inference raised-not-closed,
no per-user auth in V1, audit tail-truncation) are written down, not buried.

### Try it

`docker compose up`, open localhost, ask a scripted question, watch a plan get
built, labeled, approved, executed — and watch the PII question get denied. No
API key required (a deterministic demo planner stands in for the LLM;
one env var swaps in a real one). Five minutes, and the whole loop is visible.

---

## Show HN (draft)

**Show HN: Fondaco – let an LLM query enterprise data without the data leaving**

Fondaco is a reference architecture for using a frontier model over data it is
never allowed to see. The model is a *blind planner*: it gets the annotated
schema + the question (never rows) and returns an inspectable JSON plan. Inside
your perimeter, deterministic code validates the plan (SELECT-only,
parameterized, whitelisted), checks a classification-label egress policy, shows
it to a human to approve, executes it against a read-only DB role, applies
cardinality/query-budget guards, and writes every crossing to an append-only
hash-chained audit log. Data stays home; plans cross.

It's a working implementation, not a product — the point is the boundary and
the honesty about its limits. The threat model documents 14 attacks with
outcomes, including a critical the adversarial pass caught and fixed (a
comma-join that hid a restricted table from the label scanner). Residual risks
(aggregate inference, no per-user auth in V1, audit tail-truncation) are written
down, not hidden.

Demo is keyless: `docker compose up`, open localhost, ask a scripted question,
approve, see labeled results — and see the PII question get denied. Swap in a
real OpenAI-compatible planner (cloud or local Ollama, or behind a LiteLLM/
Bifrost gateway) with one env var.

Stack: Python 3.12, FastAPI, Postgres, Jinja+htmx, Docker Compose. Apache-2.0.

Repo: <link> · Threat model: <link to design/threat-model.md>

Would especially value scrutiny of the boundary code (`/boundary`) and the
threat model's residual-risk list.
