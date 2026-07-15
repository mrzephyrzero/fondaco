# Roadmap

**Direction, not promises.** V1 scope is frozen by `IMPLEMENTATION_PLAN.md`;
everything here is explicitly *out* of V1 and may change or never ship. Fondaco
V1 is a reference architecture — this page says where the idea goes next, so
readers can judge the shape of the thing, not commit to a release plan.

The through-line: V1 proves the boundary (a blind planner, a validated plan, a
labeled read, a human approval, an audited crossing). The four directions below
each deepen one weak seam of that boundary.

## D2 — Abstraction mode

Today the planner sees real table and column names (plus classification labels
and row counts). That is already row-free, but the *shape* of the schema still
leaves the perimeter. Abstraction mode would show the planner an abstracted,
renamed schema (opaque identifiers, typed columns, relationships) and translate
the returned plan back to real objects inside the boundary — so not even
schema naming crosses. Trade-off: planning quality vs. disclosure; worth
measuring against the scenario suite.

## D3 — Labels-at-source deepening

V1 reads classification from SQL `COMMENT` annotations (`label:<level>`). That
is convenient but shallow: labels live beside the data, not in it, and a
mislabeled column fails open only because the boundary defaults unlabeled
objects to `restricted`. D3 pushes classification into the source of truth —
catalog integrations, policy tags, column-level governance systems — so labels
are inherited, not hand-typed, and cover more than the four coarse levels.

## D4 — Attestable backends

The boundary is trusted code, but a deployer currently has to *believe* it runs
as written. D4 explores verifiable execution environments (reproducible
builds, remote attestation, confidential-compute backends) so an approver — or
an auditor — can get cryptographic evidence that the plan they approved is the
plan that ran, against the data they think, with no side path. This turns "we
promise the LLM never sees rows" into something checkable.

## ERP adapter

V1 ships one adapter (PostgreSQL) to prove the `adapter-contract.md` interface.
The natural next adapter is a real ERP surface (the original motivation:
enterprise data that genuinely cannot leave). An ERP adapter stresses the
contract where it matters — read-only enforcement at the source, coarse
statistics without row exposure, labels from a governance model — and is the
truest test that the boundary generalizes beyond a demo database.

## Parking lot

Items deferred out of scope during development land here with a one-line note.

- Per-user identity & authorization (V1 has no auth; the approver is
  self-declared, the query budget is per-session-cookie). A real deployment
  needs identity — noted in the threat model as a known limitation.
- Audit head-anchoring (external notarization of the latest hash) to close the
  tail-truncation residual risk.
