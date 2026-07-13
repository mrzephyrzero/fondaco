# Data Classification & Propagation Model

**Version:** v0 — **FROZEN**
**Status:** Frozen as-is by the human architect on 2026-07-13 (sign-off in `DECISIONS.md`). Binding for all implementation.
**Change procedure (post-freeze):** No in-place edits. Problems are filed in `STATE.md` under `INTERFACE_CHANGE_REQUEST`; only the human approves; approved changes ship as a new version of this document.

## 1. Purpose

Every piece of data inside the boundary carries a classification label. Labels decide what may leave the boundary (egress) and are propagated automatically — the LLM planner and the user cannot lower them.

## 2. Classification levels

A total order of four levels:

```
public < internal < confidential < restricted
```

| Level | Meaning (guidance, not enforcement) |
|---|---|
| `public` | Already publishable outside the organization |
| `internal` | Any employee may see it |
| `confidential` | Need-to-know within the organization |
| `restricted` | Named-individuals only (PII, payroll, secrets) |

Levels are closed: exactly these four, compared only by the order above.

## 3. Attaching labels to schema objects

- Labels attach to **tables** and **columns** in the adapter's `AnnotatedSchema` (see `adapter-contract.md`). For the Postgres adapter, v0 reads them from SQL `COMMENT` annotations of the form `label:<level>` (exact syntax owned by the adapter, surfaced uniformly in `AnnotatedSchema`).
- A column's effective label = `max(column label, its table's label)`.
- **Fail-closed default:** any table or column with no label is treated as `restricted`. Unlabeled data can never leak by omission.
- Schema annotations are data from the customer environment: the planner may see them, but boundary code treats their *content* as untrusted text (hostile-comment attacks are in scope for Phase 7).

## 4. Propagation rule (derived results)

**Max-label rule:** the label of any derived result is the maximum of the labels of all its inputs.

- `query` step result → max over the effective labels of every column read by the template. If the validator cannot statically resolve every column a template reads (e.g., `SELECT *` on an unknown view), the result is `restricted`.
- `aggregate` step result → the label of its input, unchanged. Aggregation does **not** declassify in v0: `avg(salary)` is as restricted as `salary`. (Declassification-by-aggregation is a possible future version, never an implicit behavior.)
- `present` step → carries the label of its input to the egress check.

There is no operation, at any layer, that lowers a label. Relabeling data at the source (the schema annotations) is the only way to change classification, and that is a human act outside the system.

## 5. Egress rule

Every egress endpoint (v0 has exactly one: the approval-gated results view) has a **clearance**, one of the four levels, set in configuration.

```
allow egress  ⇔  result label ≤ endpoint clearance
```

- Evaluated by `boundary/policy.py` *before* execution (static, from schema labels) and re-checked *after* execution against the actual `LabeledResult` (defense in depth).
- Deny is the default: missing clearance config, unknown label, or any evaluation error → deny with a machine-readable reason, logged to the audit trail.
- Human approval is necessary but not sufficient: an approver cannot override a policy deny in v0.

## 6. Non-goals (v0)

- No per-row or per-cell labels (schema-object granularity only).
- No user identity model / per-user clearances (one configured endpoint clearance).
- No declassification workflows, no label expiry.
- No inference-channel guarantees beyond the guards of Phase 5 (k-threshold, query budget); see README "What this does NOT protect against".
