# Decisions log

One line per decision. Dependencies require rationale (operating rule 6). Interface changes are never recorded here — they are versioned in `/design`.

## Project decisions

| Date | Decision | Rationale |
|---|---|---|
| 2026-07-13 | License: **Apache-2.0** | Human decision (plan §1 open item), recorded during Phase 0; headers + NOTICE applied in Phase 8 |
| 2026-07-13 | Unlabeled schema objects default to `restricted` | Fail closed: nothing leaks by omission (label-model.md §3, pending freeze) |
| 2026-07-13 | Aggregation does not declassify in v0 | Max-label propagation stays monotone; declassification only ever by explicit future version (label-model.md §4, pending freeze) |
| 2026-07-13 | Line endings normalized to LF via `.gitattributes` | Windows dev host, Linux runtime/CI |
| 2026-07-13 | Policy labels query steps by whole-table over-approximation (max over all columns of every FROM/JOIN table; unresolvable → `restricted`) | Sound upper bound on label-model.md §4 "columns read" — can only raise labels, never lower; column-precise resolution deferred (implementation detail, not an interface change) |
| 2026-07-13 | Audit hash chain cannot detect tail truncation from the file alone | Inherent to hash chains; noted in `boundary/audit.py` for the Phase 7 threat model (external head anchoring is the mitigation) |

## Dependencies

| Date | Package | Rationale |
|---|---|---|
| 2026-07-13 | `fastapi` | Frozen stack choice (plan §1): standard, typed, async API framework |
| 2026-07-13 | `uvicorn[standard]` | De-facto ASGI server for FastAPI |
| 2026-07-13 | `psycopg[binary]` (v3) | Modern maintained Postgres driver; native named-parameter binding matches plan-dsl.md param rules |
| 2026-07-13 | `pytest` (dev) | Standard test runner; required by plan CI (lint + pytest) |
| 2026-07-13 | `httpx` (dev) | Required by FastAPI's TestClient for the health smoke test |
| 2026-07-13 | `ruff` (dev) | Single tool for lint + format, no plugin chain |
| 2026-07-13 | `jsonschema` | Plans are "schema-validatable" by frozen stack decision (plan §1); mature reference implementation beats hand-rolling security-critical validation |

## Sign-offs

| Date | Item | Who |
|---|---|---|
| 2026-07-13 | Checkpoint P0 human sign-off: three design docs frozen v0 as-is | Human architect (via session approval) |
