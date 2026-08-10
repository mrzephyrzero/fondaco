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
