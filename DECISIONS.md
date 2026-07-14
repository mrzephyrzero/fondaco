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
| 2026-07-14 | Demo dataset keeps PII in its own `customers` table (`restricted`); operational tables are uniformly `internal` | The whole-table label over-approximation would otherwise mark every orders query `restricted`; normalization is also what a real deployment would do |
| 2026-07-14 | `fondaco_ro` password is hardcoded demo-grade in `03_readonly_role.sql` | initdb `.sql` scripts cannot read env vars; documented in `.env.example`; production hardening out of V1 scope |
| 2026-07-14 | `decimal.Decimal` accepted as numeric in executor aggregates | Postgres `numeric` columns arrive as Decimal; exact arithmetic is preferable for money; bool still explicitly excluded |
| 2026-07-14 | Compose DB stays unpublished; local integration testing uses a gitignored `docker-compose.override.yml` | Keeps the committed boundary statement (DB reachable only from the compose network) while allowing host-side tests; CI uses a service container |
| 2026-07-14 | LLM returns steps only; the boundary builds the plan envelope | `plan_id` is boundary-assigned per plan-dsl.md §2; question copied verbatim — the model cannot forge ids or restate the question |
| 2026-07-14 | Default planner endpoint: Anthropic OpenAI-compat (`claude-sonnet-5`), human decision | Human chose Anthropic for the live checkpoint run; any OpenAI-compatible URL is a config swap |
| 2026-07-14 | Scenario 10 deliberately policy-denied | The deny path is part of the demo; checkpoint margin computed over the 9 answerable questions |
| 2026-07-14 | Plan store is in-memory; only the audit log persists | V1 demo scope: the approval queue does not survive an app restart, the tamper-evident record does (named volume) |
| 2026-07-14 | No authentication in V1; approver identity is a self-declared form field | Approval flow is the demo, identity/authz is deployment-specific; documented limitation, revisited in Phase 7/8 |
| 2026-07-14 | htmx 2.0.4 vendored into `api/ui/static` | No CDN: the five-minute stranger demo must work offline; no build chain (frozen stack) |
| 2026-07-14 | Compose planner default: host Ollama via `host.docker.internal` | Demo works without any API key (Anthropic credits still pending); cloud API is a `.env` override |
| 2026-07-14 | k-threshold applies to **every** aggregate result, not only planner-facing ones | Plan §Phase-5 scopes it to "aggregates returned toward the planner in repair loops", but our repair loop feeds back only validation errors (P3 canary), so that scope would guard nothing; the reader in the UI is the attacker. Superset of the requirement, same intent |
| 2026-07-14 | Small groups are dropped, not masked-in-place | A masked cell beside a visible count still leaks the count; only removal kills the binary-search primitive |
| 2026-07-14 | Query budget is in-memory, per session cookie; guards fail closed (k=0/negative → default) | V1 demo scope: no auth, budget resets on cookie clear (documented residual risk); a guard can be tuned but never disabled |
| 2026-07-14 | Guard defaults k=5, query_budget=20 | Reasonable demo values; small enough to demonstrate suppression and the hard stop, configurable via env |

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
| 2026-07-14 | `httpx` promoted dev → runtime | Planner LLM client; raw HTTP to any OpenAI-compatible endpoint keeps the outbound surface auditable — no vendor SDK |
| 2026-07-14 | `jinja2` | Frozen stack choice (plan §1): server-rendered approval/audit UI |
| 2026-07-14 | `python-multipart` | FastAPI form parsing for the approval flow (ask/approve/reject forms) |

## Sign-offs

| Date | Item | Who |
|---|---|---|
| 2026-07-13 | Checkpoint P0 human sign-off: three design docs frozen v0 as-is | Human architect (via session approval) |
