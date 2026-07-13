# Plan DSL Specification

**Version:** v0 — **FROZEN**
**Status:** Frozen as-is by the human architect on 2026-07-13 (sign-off in `DECISIONS.md`). Binding for all implementation.
**Change procedure (post-freeze):** No in-place edits. Implementation problems are filed in `STATE.md` under `INTERFACE_CHANGE_REQUEST` with the problem and a proposed change; only the human approves, and any approved change ships as a new version (v1, v2, …) of this document.

## 1. Purpose

A *plan* is the only artifact the LLM planner may produce and the only input the executor accepts. Plans are inspectable, diffable JSON. Row data never appears in a plan; a plan describes *what to read*, never *what was read*.

## 2. Plan envelope

```json
{
  "dsl_version": "v0",
  "plan_id": "<uuid4, assigned by the boundary, not the LLM>",
  "question": "<verbatim user question>",
  "steps": [ ... ]
}
```

- `steps` is an ordered, non-empty list. Max 10 steps per plan.
- Each step has a unique `id` (string, `^s[0-9]+$`) and may reference only *earlier* step ids (no cycles by construction).
- The final step MUST be of type `present`; `present` may appear only once, as the last step.
- Unknown fields anywhere in the plan → validation failure (fail closed).

## 3. Step types

Exactly three step types exist. Anything else fails validation.

### 3.1 `query` — read-only SQL template

```json
{
  "id": "s1",
  "type": "query",
  "template": "SELECT region, status FROM orders WHERE order_date >= %(since)s",
  "params": { "since": { "type": "date", "value": "2026-01-01" } }
}
```

- `template`: a single SQL statement that MUST begin with `SELECT` (after whitespace/comment stripping; comments are themselves rejected). One statement only — no `;`.
- Placeholders MUST use named-parameter form (`%(name)s`). String interpolation, f-string-style braces, or literal values derived from the question where a parameter would do are validation errors.
- `params`: every placeholder in the template must be declared here, and every declared param must appear in the template. Allowed types: `string`, `int`, `float`, `bool`, `date`, `timestamp`. Values are passed to the driver as bound parameters, never spliced into the template.
- Forbidden anywhere in the template: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `GRANT`, `COPY`, `CALL`, `DO`, `INTO`, `;`, comments (`--`, `/*`). Enforcement is deny-by-default: the validator whitelists structure, it does not merely blacklist keywords.

### 3.2 `aggregate` — in-boundary reduction of a prior step's result

```json
{
  "id": "s2",
  "type": "aggregate",
  "input": "s1",
  "group_by": ["region"],
  "ops": [ { "op": "count", "column": "*", "as": "n_orders" } ]
}
```

- `input`: id of one earlier `query` or `aggregate` step.
- `ops`: non-empty list; `op` ∈ { `count`, `sum`, `avg`, `min`, `max` }. `column` names a column of the input result (or `"*"` for `count`). `as` names the output column.
- `group_by`: possibly empty list of input columns.
- Executed by boundary code, never by the database and never by the LLM.

### 3.3 `present` — declare what leaves the boundary

```json
{
  "id": "s3",
  "type": "present",
  "input": "s2",
  "format": "table",
  "title": "Orders per region since 2026-01-01"
}
```

- `input`: id of one earlier step; its labeled result is the candidate output.
- `format` ∈ { `table`, `scalar` }. `title`: human-readable, shown in the approval UI.
- Egress is still gated by the label model (see `label-model.md`) and human approval; `present` declares intent, it grants nothing.

## 4. Validation rules (summary)

A plan is valid only if ALL hold: well-formed JSON matching the schema; known `dsl_version`; ≤ 10 steps; unique sequential step ids; references only to earlier steps; exactly one `present`, in final position; every `query` passes the template rules of §3.1; every `aggregate`/`present` input exists. Any failure → reject with a machine-readable reason; never repair silently.

## 5. Non-goals (explicit, permanent for v0)

- No writes, no DDL, no transactions.
- No loops, branches, or conditional steps.
- No external calls (HTTP, files, other adapters) from within a plan.
- No free-form SQL passthrough; no multi-statement templates.
- No LLM-visible row data at any point in the plan lifecycle.
