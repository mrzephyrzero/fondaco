# Project state

## Status

Fondaco V1 is a **reference architecture with a working implementation**, not a
maintained product. The build is complete: the boundary, executor, planner, API,
guards and audit log are all implemented, and the adversarial pass documented in
[`design/threat-model.md`](design/threat-model.md) is closed — 14 attacks, one
critical found and fixed (CRIT-1), no known critical findings open.

Two release items are open and are the maintainer's to close, stated here rather
than left implied:

- The clean-machine test (fresh clone → `docker compose up` → walkthrough in ≤ 5
  minutes) has been run by the author, but not yet repeated by anyone else.
- GitHub private vulnerability reporting — the sole disclosure channel named in
  [`SECURITY.md`](SECURITY.md) — is not yet enabled on the repository.

## INTERFACE_CHANGE_REQUEST

This section is the register named by the change procedure of the three frozen
interfaces (`design/plan-dsl.md`, `design/label-model.md`,
`design/adapter-contract.md`). Problems with a frozen interface are filed here;
only the maintainer approves; an approved change ships as a new version of the
document it affects, never as a silent in-place edit.

**None during the build.** All three interfaces shipped v0 unchanged. CRIT-1 was
a code fix, not an interface change.

### `design/plan-dsl.md` v0 → v1 — approved and shipped

- **Flaw:** §2 required step ids only to be unique and to match `^s[0-9]+$`,
  admitting a non-contiguous set such as `s1, s3, s5`. §4 of the same document
  said "unique **sequential** step ids", and `boundary/validator.py` has always
  enforced exactly `s1 … sN` in order. The document contradicted itself, and the
  implementation followed §4.
- **Found by:** a technical audit of the finished repo.
- **Resolution:** §2 restated to match §4 and the code. This narrows what v0
  nominally permitted, so no previously accepted plan becomes invalid, and no
  behaviour changed. Shipped as **v1** with a changelog, per the document's own
  change procedure.

### `design/label-model.md` v0 → v1 — approved and shipped

- **Flaw:** §5 stated the egress rule was re-checked after execution against the
  actual `LabeledResult`, as defense in depth. No such re-check exists in the
  code: approval calls `run_plan` and then renders and audits the result without
  a second `evaluate(...)`. The adapter does recompute a label, but from the same
  `query_label` used by the static check, so the two layers could not have
  disagreed — not independent defense in depth even in intent. This is why a
  single flaw in `query_label` (CRIT-1) defeated both checks at once.
- **Found by:** a technical audit of the finished code, not by implementation.
- **Resolution:** documentation corrected to match the code — documentation
  moves, code does not. Shipped as **v1** under the document's own change
  procedure (version bump + changelog). The egress rule, the level order and the
  propagation rules are byte-for-byte v0.
- **Not done:** the missing re-check was *not* implemented. Adding one would be a
  code change, and a genuinely independent one would need a second labeling path
  that does not route through `query_label` — both out of scope.

### `design/adapter-contract.md` v0 → v1 — OPEN, awaiting maintainer approval

- **Flaw / need:** §2.1 says `get_schema` "MUST NOT include row data or sample
  values". The governance-firewall evolution (Step 1) asks for semantic ENUM
  metadata on low-cardinality columns so the planner filters on codes that
  exist instead of guessing (a wrong guess is a valid plan that returns zero
  rows). Real values in the schema contradict §2.1 as written.
- **Found by:** implementation of Step 1 (requested by the maintainer).
- **Implemented, pending this approval:** `executor/adapters/contract.py`
  `Column.enum_values` / `Column.business_description`, refused at
  construction above `internal`; re-checked in `planner/client.py`. No adapter
  populates them yet. The interface document itself was **not** edited.
- **Proposed change (v1), for the maintainer to apply:**
  - §2.1 signature: `Column(name, sql_type, label, comment, enum_values = None,
    business_description = None)`.
  - §2.1, replacing "MUST NOT include row data or sample values": *MUST NOT
    include row data or sample values, with one exception.
    `enum_values: list[str] | None` is the complete, closed set of distinct
    values a column holds; `business_description: str | None` explains them
    (e.g. "01=Open, 02=In progress"). Both MAY be set only when the column's
    effective label — max(column, table), label-model.md §3 — is `public` or
    `internal`; `enum_values` only for ≤ 30 distinct strings of ≤ 64
    characters; `business_description` ≤ 1000 characters. Both are untrusted
    text like `comment`. Consumers that forward the schema to an LLM MUST
    re-check the effective label before serializing them.*
  - Changelog entry for v1; nothing else in the contract changes, and a v0
    `Column` remains a valid v1 `Column`.

### `design/plan-dsl.md` — `WITH` templates: not changed, noted

- **Observation:** the AST analyser resolves CTEs (`WITH … SELECT`), but §3.1
  requires every template to *begin with* `SELECT`, so the validator still
  rejects them. The policy engine handles CTEs correctly as defense in depth.
- **Status:** no change requested. Admitting `WITH` would be a plan-dsl v2 and
  needs the maintainer's explicit approval.

