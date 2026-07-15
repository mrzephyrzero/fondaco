# STATE

_Single source of progress truth. Updated at the end of every work block (operating rule 2)._

- **Current phase:** 8 — Release readiness: **authoring COMPLETE; launch gate pending human sign-off** (see checkpoint below)
- **Last completed checkpoint:** P7. **P8 authoring done**, CI to confirm; the two human-gated launch-gate items remain open by design.
- **Project status:** V1 reference architecture **feature-complete**. All code phases (P0–P7) closed and green; P8 applies license + finalizes the public docs. What remains is human launch-gate action, not engineering.

## Checkpoint P8 status (launch gate)

| Item | Status |
|---|---|
| Clean-machine test repeated by a human who is **not the author** | ⬜ **pending human** — author pre-flight passed on commit `563a1cf` (fresh GitHub clone with the P8 headers → `docker compose up` healthy in **41s** → walkthrough: Q1 executed/`internal`, Q10 denied/409, audit chain verified), but the "different human" requirement is the human's to perform and check off |
| Threat model, README, SECURITY.md reviewed in one final pass | ✅ authoring pass done (consistent claim / residual risks / disclosure; README duplicate bullet fixed; threat-model status finalized) — ⬜ **final human sign-off pending** |
| `STATE.md` archived; repo history clean | ✅ this file is the final state; `git status` clean, phase-prefixed history, no secrets/scratch tracked |
| Enable GitHub **private vulnerability reporting** in repo settings | ⬜ **pending human** — SECURITY.md documents it as the sole channel; the maintainer must toggle it on (Settings → Code security) |

Verification 2026-07-14: 153 tests green (2 live-key tests skip without a key), ruff + format clean. LICENSE + NOTICE present; SPDX header on every source file; **no personal email anywhere in the repo** (`git grep` clean).

## What Phase 8 did

- **License applied:** Apache-2.0 SPDX header (`Copyright 2026 the Fondaco contributors`) on all 43 source/config files, replacing the placeholders; added `NOTICE`; `pyproject.toml` declares `license = "Apache-2.0"`; `CLAUDE.md` convention line updated.
- **`SECURITY.md` finalized:** GitHub private vulnerability reporting as the **sole** disclosure channel (no personal/email address, per human decision); honest solo-maintainer no-SLA response expectation; reference-architecture scope; links the threat model.
- **`ROADMAP.md`:** the four-design narrative (D2 abstraction mode, D3 labels-at-source, D4 attestable backends, ERP adapter) as direction-not-promises, plus parking lot (auth, audit head-anchoring).
- **`LAUNCH_DRAFTS.md`:** blog #1 draft + Show HN draft (top-level file; no new directory), leading with the boundary and the honest threat model (CRIT-1 as credibility).
- **Consistency pass:** fixed the duplicated README residual-risk bullet; threat-model status line finalized.

## For the human — to close the launch gate

1. **Enable** private vulnerability reporting in GitHub repo settings (SECURITY.md relies on it).
2. **Have a non-author** run the clean-machine test (fresh clone → `docker compose up` → walkthrough) and confirm ≤5 min.
3. **Read** `design/threat-model.md`, `README.md`, `SECURITY.md` once more and sign off.
4. **Fill** the `<link>` placeholders in `LAUNCH_DRAFTS.md` before publishing.
Then the V1 launch gate is fully closed.

## Open questions (for the human)

_None (both prior questions resolved: Anthropic cloud funded + verified; Python 3.12 local parity done)._

## INTERFACE_CHANGE_REQUEST

_None across the entire build. The three frozen interfaces (plan-dsl, label-model, adapter-contract) shipped v0 unchanged; CRIT-1 in Phase 7 was a code fix, not an interface change._
