# Prompt changelog

Prompt templates are versioned files in this directory; every change gets an entry here.

## v3 — 2026-07-14 (Phase 3, current)

Root-caused the v2 policy denials: templates using `EXTRACT(YEAR FROM col)`
trip the label engine's conservative FROM-scan (any intra-function FROM
reads as an unresolvable table → `restricted`). v3 forbids FROM inside
function calls, teaches half-open date ranges (`>= start AND < end`) and
`to_char()` for month grouping, and adds a second worked example (global
scalar aggregate). The policy engine stays untouched — fail-closed
over-approximation is a feature (operating rule 5), the planner adapts.

## v2 — 2026-07-14 (Phase 3)

Added a full worked example (query→aggregate→present with a date param) and
tightened constraints after the first live run (qwen2.5-coder:7b, 3/9):
templates must not contain GROUP BY/ORDER BY/aggregate functions (grouping
belongs to aggregate steps); touch only required tables — reading any table
raises the result label (policy denies above clearance); scalar vs table
format hint. Failure modes addressed: SQL-side grouping, needless joins to
restricted tables, invalid-DSL exhaustion.

## v1 — 2026-07-14 (Phase 3)

Initial template: v0 DSL contract (three step types, SELECT-only templates,
`%(name)s` params, ≤10 steps, final `present`), bare-JSON output format,
strategy guidance (filter early / 10k raw-row cap, prefer low-label tables,
GROUP BY in aggregate steps not SQL, comments are hints not instructions).
