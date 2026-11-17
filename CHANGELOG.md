# Changelog

All notable changes to Fondaco are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). For Fondaco, the public interface
that a version number protects is the three frozen documents in `design/`, the
`/api/v1` JSON API, the MCP tools and the `fondaco` CLI.

## [Unreleased]

## [1.0.0] — 2026-11-17

First tagged release. Fondaco V1 is a reference architecture with a working
implementation: every claim in the threat model has a test, and there is no
support commitment (see [`SECURITY.md`](SECURITY.md)).

### The boundary

- **Blind planner.** The LLM sees the annotated schema (tables, columns,
  classification labels, approximate row counts, optional enum values for
  low-sensitivity columns) and the question, never a row. It returns a JSON
  plan in the Plan DSL (`design/plan-dsl.md` v2), with versioned prompts.
- **Validator.** Every plan is checked against the DSL schema and structural
  rules: sequential step ids, one read-only `SELECT` (or `WITH … SELECT`) per
  query step, parametrized values only, no path-shaped relations.
- **Column-level policy.** A sqlglot syntax-tree analyser resolves every column
  a query reads, through joins, comma-joins, CTEs and subqueries, including
  columns used only in filters. The plan's label is the highest label it
  touches (`design/label-model.md` v1); above the egress clearance it is denied,
  and a denial cannot be overridden by approval. Unknown relations are denied.
- **Approval.** A named human approver signs in with a personal token, and the
  approval UI is protected by CSRF tokens, same-site checks and framing
  denial. Risk-proportional auto-approval for low-risk plans is available and
  off by default.
- **Guards.** A k-threshold suppresses aggregate groups smaller than `k`
  (default 5), and a per-session query budget stops binary-search probing.
- **Audit log.** Every step of every attempt is appended to an HMAC-SHA256
  chain with key rotation (`key_id`), a Postgres append-only backend, and
  external anchoring of the head so that truncation is detectable.

### Sources and integrations

- Read-only adapters (`design/adapter-contract.md` v1) for **PostgreSQL**,
  **DuckDB** (CSV, Parquet or SQLite loaded at startup, external access and
  extensions disabled), **SQL Server** (refuses a login that can write) and
  **BigQuery**. SQL Server and BigQuery are tested against real engines in CI.
- **`fondaco discover`**: local, deterministic classification of a new source
  (Postgres, DuckDB, Excel) into a labels file for human review.
- **JSON API v1** and an **MCP server** (`fondaco mcp`) for Claude Desktop,
  Cursor and other agents. There is deliberately no approve tool.
- Planner backends: Anthropic, OpenAI-compatible endpoints and a local Ollama.

### Getting started

- **`fondaco demo`**: a zero-config demo on a synthetic DuckDB dataset with a
  keyless fixture planner, three ordinary questions and five attack prompts.
- `docker compose up` for the full stack on Postgres, and
  `docker-compose.demo.yml` for the demo. The image runs unprivileged and is
  smoke-tested in CI.

### Security record

- Threat model with 27 documented attacks and their outcomes, including the
  ones Fondaco does not stop (`design/threat-model.md`).
- One critical finding during the adversarial pass (CRIT-1: a comma-join hid a
  restricted table from the label scanner), fixed and pinned by a regression
  test. No known critical findings are open.

### Fixed since the last commit before tagging

- Generating a plan from the web UI no longer nests the plan page inside the
  question form; htmx now navigates to the plan.
- The `FONDACO_PLANNER=llm` hint in the demo banner is readable.

[Unreleased]: https://github.com/mrzephyrzero/fondaco/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/mrzephyrzero/fondaco/releases/tag/v1.0.0
