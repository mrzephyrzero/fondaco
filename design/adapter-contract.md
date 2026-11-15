# Adapter Contract

**Version:** v1 — **FROZEN**
**Supersedes:** v0 (frozen as-is by the human architect on 2026-07-13, sign-off in `DECISIONS.md`). Binding for all implementation.
**Change procedure (post-freeze):** No in-place edits. Problems are filed in `STATE.md` under `INTERFACE_CHANGE_REQUEST`; only the human approves; approved changes ship as a new version of this document.

**Changelog**

- **v1** — §2.1 extended with semantic ENUM metadata. `Column` gains two optional fields, `enum_values` and `business_description`, so the planner can filter low-cardinality code columns on values that exist instead of guessing (a wrong guess is a valid plan that returns zero rows). This is the single, bounded exception to "MUST NOT include row data or sample values": allowed only when the column's effective label is `public` or `internal`, at most 30 values. Filed in `STATE.md` as an `INTERFACE_CHANGE_REQUEST` and approved by the human maintainer on 2026-11-15. Nothing else changed; a v0 `Column` is a valid v1 `Column`.
- **v0** — initial frozen version, 2026-07-13.

## 1. Purpose

An adapter is the only code that touches a data source. It exposes exactly three operations to the executor and is **read-only by contract**: no method may mutate the source, and adapters must additionally enforce read-only access at the source itself where possible (e.g., a Postgres role without write grants — defense in depth, not only in code).

## 2. Interface

```python
class Adapter(Protocol):
    def get_schema(self) -> AnnotatedSchema: ...
    def execute(self, step: QueryStep) -> LabeledResult: ...
    def capabilities(self) -> Capabilities: ...
```

Adapters receive only validated `query` steps (see `plan-dsl.md`). `aggregate` and `present` are executed by boundary/executor code and never reach an adapter.

### 2.1 `get_schema() -> AnnotatedSchema`

Returns the queryable surface with classification labels attached (see `label-model.md`):

- `AnnotatedSchema`: list of `Table(name, label, columns: list[Column], comment)` with `Column(name, sql_type, label, comment, enum_values = None, business_description = None)`.
- Labels are resolved by the adapter from source-native annotations; objects without an annotation are returned with label `restricted` (fail closed).
- May include coarse statistics (row counts) for planner context. MUST NOT include row data or sample values, with the single exception below.
- **Semantic ENUM metadata (v1).** `enum_values: list[str] | None` is the complete, closed set of distinct values a column holds; `business_description: str | None` explains them (e.g. `"01=Open, 02=In progress, 03=Blocked"`). Both MAY be set only when the column's effective label — `max(column label, table label)`, `label-model.md` §3 — is `public` or `internal`; `enum_values` only for at most 30 distinct strings of at most 64 characters each; `business_description` at most 1000 characters. Setting either on a `confidential` or `restricted` column is forbidden, and the shared contract types refuse to construct such a column or table. Both fields are untrusted text, like `comment`. Consumers that forward the schema to an LLM MUST re-check the effective label before serializing them.
- `comment` fields are untrusted text (customer-controlled); consumers must treat them accordingly.

### 2.2 `execute(step) -> LabeledResult`

- Input: a single validated `query` step. The adapter binds `params` as driver-level parameters — never string interpolation — and runs the template as one read-only statement.
- Output: `LabeledResult(columns: list[str], rows: list[tuple], label: Label, row_count: int, digest: str)` where `label` is computed per the max-label rule and `digest` is a SHA-256 of the canonicalized result (for audit, so results are referenceable without storing them in the log).
- Limits: the adapter enforces a configurable `max_rows` (default 10 000) and statement timeout (default 30 s). Exceeding either → `AdapterError`, not truncation.

### 2.3 `capabilities() -> Capabilities`

Static declaration used by validator and planner prompt:

- `Capabilities(dsl_versions: list[str], param_types: list[str], max_rows: int, read_only: bool)`.
- `read_only` MUST be `True`; the executor refuses adapters that report otherwise.

## 3. Error semantics (fail closed)

- All failures raise `AdapterError(kind, message)` with `kind` ∈ { `connection`, `timeout`, `limit_exceeded`, `execution`, `schema` }.
- `message` MUST NOT embed row data or parameter values (error text can egress toward logs and the repair loop; see Phase 7 threat "exfiltration via error messages"). Source-driver messages are mapped/sanitized, not passed through.
- No partial results: an error yields no `LabeledResult` at all.
- Adapters MUST NOT retry writes-disguised-as-reads (`SELECT ... INTO`, functions with side effects): anything the adapter cannot prove read-only is refused.

## 4. Conformance

An adapter is conformant when it passes the shared contract test suite (`tests/integration/test_adapter_contract.py`, built in Phase 2), including: labels present on every schema object, unlabeled-object fallback to `restricted`, parametrized execution only, write attempts fail at the source layer, `max_rows`/timeout enforcement, and sanitized errors.

## 5. Non-goals (v0)

- No write or DDL capability, ever, in any version of this contract.
- No streaming/pagination (bounded results only).
- No cross-adapter joins (one adapter per plan in v0).
- No adapter-initiated network egress beyond its own data source.
