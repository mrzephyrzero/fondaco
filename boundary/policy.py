# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Label/egress policy engine (design/label-model.md §2–5).

Evaluates a *validated* plan against schema labels and an endpoint
clearance. Deny is the default on every path: unknown labels, unknown
relations, unresolvable SQL references, and internal faults all deny.

Query-step labeling is column-precise (label-model.md §4, "max over the
effective labels of every column read by the template"). Each template is
parsed by `boundary.sql_ast` into a syntax tree; sqlglot's qualifier resolves
every column reference — through aliases, CTEs, derived tables, LATERAL,
explicit and comma joins, correlated and nested subqueries, set operations —
to the base table it comes from. The step label is then

    max( effective label of every column referenced anywhere in the template
         — SELECT, WHERE, JOIN … ON, GROUP BY, HAVING, ORDER BY, subqueries —
         , label of every base table the template reads )

A column's effective label is max(column label, its table's label) (§3), and
the table label is a floor even when no column is named (`count(*)` over an
internal table is internal). Reading a column in a predicate counts as reading
it: a filter on a restricted column is an inference channel on that column.

Three outcomes are distinct on purpose:
  * a relation the annotated schema does not contain — a table function
    (`read_csv(...)`, `unnest(...)`, `generate_series(...)`), a schema- or
    catalog-qualified name (`pg_catalog.pg_user`, `information_schema.tables`,
    `public.customers`), `VALUES`, or simply an unknown name — is an absolute
    deny (`unknown_relation`), whatever the clearance;
  * a reference the analyser cannot resolve (unknown or ambiguous column,
    whole-row reference, unexpanded `*`) labels the step `restricted`;
  * anything else gets its computed label and goes to the egress rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from boundary.sql_ast import SqlRejected, parse_template, resolve_dialect


class LabelError(ValueError):
    """Raised for any string that is not exactly one of the four levels."""


class Label(IntEnum):
    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3

    @classmethod
    def parse(cls, raw: object) -> Label:
        if isinstance(raw, str) and raw in _LEVELS:
            return _LEVELS[raw]
        raise LabelError(f"unknown label: {raw!r}")


_LEVELS: dict[str, Label] = {
    "public": Label.PUBLIC,
    "internal": Label.INTERNAL,
    "confidential": Label.CONFIDENTIAL,
    "restricted": Label.RESTRICTED,
}


@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    plan_label: str | None
    reason_code: str
    detail: str


def _deny(reason_code: str, detail: str, plan_label: str | None = None) -> PolicyDecision:
    return PolicyDecision(
        allow=False, plan_label=plan_label, reason_code=reason_code, detail=detail
    )


def _parse_or_restricted(raw: object) -> Label:
    """Missing/unknown labels degrade upward, never downward (fail closed)."""
    if raw is None:
        return Label.RESTRICTED
    try:
        return Label.parse(raw)
    except LabelError:
        return Label.RESTRICTED


@dataclass(frozen=True)
class QueryAnalysis:
    """What the analyser established about one template. Never carries row data."""

    label: Label
    unknown_relations: tuple[str, ...] = ()
    columns: tuple[tuple[str, str], ...] = ()  # (base table, column) pairs read
    tables: tuple[str, ...] = ()  # base tables read
    resolved: bool = True  # False → label was forced to restricted
    reason: str = ""  # why resolution failed, when it did


def _unresolved(reason: str, **kwargs) -> QueryAnalysis:
    return QueryAnalysis(label=Label.RESTRICTED, resolved=False, reason=reason, **kwargs)


@dataclass(frozen=True)
class _TableLabels:
    label: Label
    columns: dict[str, Label]  # effective labels: max(column, table)


def _labels_index(schema_labels: dict) -> dict[str, _TableLabels]:
    """Schema labels keyed by the exact catalog name the adapter reported.

    Names are exact (they come from the source catalog, e.g. pg_class.relname)
    and are matched against the dialect-normalized tree, which is how the
    database itself resolves identifiers. A missing/unknown label is
    restricted; a column label can raise its table's, never lower it.
    """
    index: dict[str, _TableLabels] = {}
    if not isinstance(schema_labels, dict):
        return index
    for name, table in schema_labels.items():
        if not isinstance(name, str):
            continue
        if not isinstance(table, dict):
            index[name] = _TableLabels(Label.RESTRICTED, {})
            continue
        table_label = _parse_or_restricted(table.get("label"))
        raw_columns = table.get("columns") or {}
        columns: dict[str, Label] = {}
        if isinstance(raw_columns, dict):
            for column, column_label in raw_columns.items():
                if isinstance(column, str):
                    columns[column] = max(table_label, _parse_or_restricted(column_label))
        index[name] = _TableLabels(table_label, columns)
    return index


def _unknown_names(tree: exp.Expression, known: dict) -> list[str]:
    """Bare relation names that are neither in the schema nor a CTE of the template.

    Functions in FROM, table-valued constructs and qualified names were already
    refused by `boundary.sql_ast.foreign_relations` during parsing.
    """
    cte_names = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    return [
        table.name[:128]
        for table in tree.find_all(exp.Table)
        if table.name not in known and table.name not in cte_names
    ]


def _resolve_source(scope: Scope, name: str) -> object | None:
    """The source `name` refers to, searching enclosing scopes (correlation)."""
    current: Scope | None = scope
    while current is not None:
        source = current.sources.get(name)
        if source is not None:
            return source
        current = current.parent
    return None


def _projection_aliases(column: exp.Column) -> set[str]:
    select = column.find_ancestor(exp.Select)
    if select is None:
        return set()
    return {e.alias for e in select.expressions if isinstance(e, exp.Alias)}


def analyze_query(template: str, schema_labels: dict, dialect: str | None = None) -> QueryAnalysis:
    """Column-precise label of one query template. Never raises; fails closed."""
    try:
        return _analyze(template, schema_labels, dialect)
    except Exception as exc:  # fail closed: an internal fault labels restricted
        return _unresolved(f"analysis fault: {type(exc).__name__}")


def _analyze(template: str, schema_labels: dict, dialect: str | None) -> QueryAnalysis:
    try:
        name = resolve_dialect(dialect)
        tree = parse_template(template, name)
    except SqlRejected as exc:
        return _unresolved(exc.code, unknown_relations=exc.relations)
    index = _labels_index(schema_labels)

    tree = normalize_identifiers(tree, dialect=name)
    unknown = _unknown_names(tree, index)
    if unknown:
        return _unresolved("unknown_relation", unknown_relations=tuple(dict.fromkeys(unknown)))

    mapping = {t: {c: "UNKNOWN" for c in labels.columns} for t, labels in index.items()}
    try:
        qualified = qualify(
            tree,
            schema=MappingSchema(mapping, dialect=name, normalize=False),
            dialect=name,
            validate_qualify_columns=True,
            expand_stars=True,
            quote_identifiers=False,
            identify=False,
        )
        scopes = list(traverse_scope(qualified))
    except SqlglotError as exc:
        # Unknown or ambiguous column, unresolvable alias, … — §4: restricted.
        return _unresolved(f"unresolved_reference: {type(exc).__name__}")
    if not scopes:
        return _unresolved("no_scope")

    label = Label.PUBLIC
    tables: list[str] = []
    columns: list[tuple[str, str]] = []
    processed: set[int] = set()
    for scope in scopes:
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                entry = index.get(source.name)
                if entry is None:  # pre-check makes this unreachable; fail closed anyway
                    return _unresolved("unknown_table")
                tables.append(source.name)
                label = max(label, entry.label)
        for column in scope.columns:
            processed.add(id(column))
            if not column.table:
                return _unresolved(f"unqualified_reference:{column.name[:64]}")
            source = _resolve_source(scope, column.table)
            if isinstance(source, Scope):
                continue  # derived column: its inputs are labeled in their own scope
            if not isinstance(source, exp.Table):
                return _unresolved("unresolved_source")
            entry = index.get(source.name)
            column_label = entry.columns.get(column.name) if entry else None
            if column_label is None:
                return _unresolved("unknown_column")
            columns.append((source.name, column.name))
            label = max(label, column_label)

    for node in qualified.walk():
        if isinstance(node, exp.TableColumn):
            # Whole-row reference (`SELECT e FROM employees e`, `count(e)`,
            # `(e).salary`): reads every column without naming one.
            return _unresolved("whole_row_reference")
        if isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count):
            return _unresolved("unexpanded_star")
        if isinstance(node, exp.Column) and id(node) not in processed:
            # Not seen by any scope. The only legitimate case is a reference to
            # a projection alias (ORDER BY n); anything else fails closed.
            if node.table or node.name not in _projection_aliases(node):
                return _unresolved("unscoped_column")

    if not tables:
        # Reads no relation at all (`SELECT 1`). Nothing to label it by, so
        # the v0 rule stands: what cannot be attributed is restricted.
        return _unresolved("no_relation")
    return QueryAnalysis(
        label=label,
        columns=tuple(dict.fromkeys(columns)),
        tables=tuple(dict.fromkeys(tables)),
    )


def query_label(template: str, schema_labels: dict, dialect: str | None = None) -> Label:
    """Public labeling helper, shared with the executor.

    Column-precise per label-model.md §4; anything unresolvable or unknown is
    restricted. Never lowers a label below what §3–§4 assign.

    Note that the executor sharing this function means the label the adapter
    attaches to a result is not an independent check — see label-model.md §5.
    """
    return analyze_query(template, schema_labels, dialect).label


class UnknownRelation(Exception):
    """A query step reads a relation outside the annotated schema."""

    def __init__(self, step_id: str, relations: tuple[str, ...]) -> None:
        self.step_id = step_id
        self.relations = relations
        super().__init__(f"{step_id}: {', '.join(relations)}")


def step_labels(plan: dict, schema_labels: dict, dialect: str | None = None) -> dict[str, Label]:
    """Per-step labels for a validated plan (query → analysis; others inherit).

    Raises KeyError on dangling references and UnknownRelation when a query
    reads outside the annotated schema — callers must treat both as deny.
    Public so the approval UI renders exactly what policy computed.
    """
    labels: dict[str, Label] = {}
    for step in plan["steps"]:
        if step["type"] == "query":
            analysis = analyze_query(step["template"], schema_labels, dialect)
            if analysis.unknown_relations:
                raise UnknownRelation(step["id"], analysis.unknown_relations)
            labels[step["id"]] = analysis.label
        else:  # aggregate / present inherit; aggregation never declassifies
            labels[step["id"]] = labels[step["input"]]
    return labels


def evaluate(
    plan: dict, schema_labels: dict, clearance: object, dialect: str | None = None
) -> PolicyDecision:
    """Decide egress for a validated plan. Never raises; any fault → deny."""
    try:
        try:
            clearance_label = Label.parse(clearance)
        except LabelError:
            return _deny("unknown_clearance", f"clearance is not a known level: {clearance!r}")
        try:
            dialect = resolve_dialect(dialect)
        except SqlRejected as exc:
            return _deny(exc.code, exc.detail)

        try:
            labels = step_labels(plan, schema_labels, dialect)
        except KeyError:
            return _deny("missing_step", "a step references an unknown input")
        except UnknownRelation as exc:
            # Absolute: no clearance, not even restricted, admits a read
            # outside the annotated schema.
            return _deny(
                "unknown_relation",
                f"{exc.step_id} reads outside the annotated schema: {', '.join(exc.relations)}",
            )

        plan_label = labels[plan["steps"][-1]["id"]]
        result_name = plan_label.name.lower()
        clearance_name = clearance_label.name.lower()
        if plan_label <= clearance_label:
            return PolicyDecision(
                allow=True,
                plan_label=result_name,
                reason_code="allow",
                detail=f"result label {result_name} <= clearance {clearance_name}",
            )
        return _deny(
            "label_exceeds_clearance",
            f"result label {result_name} > clearance {clearance_name}",
            plan_label=result_name,
        )
    except Exception as exc:  # fail closed: an internal fault is a deny
        return _deny("policy_error", f"internal policy fault: {type(exc).__name__}")
