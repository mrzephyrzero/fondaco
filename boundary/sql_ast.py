# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Strict, parse-only SQL front end shared by the validator and the policy engine.

Templates are LLM output and therefore hostile. This module turns one into a
sqlglot syntax tree, or refuses. It never executes, transpiles, or rewrites
SQL for execution: the tree exists only so the boundary can reason about what
the template reads. The text that reaches the database is still the original
template, bound with driver-level parameters by the adapter.

Deny by default:
  * exactly one statement, whose root is a SELECT or a set operation
    (UNION / INTERSECT / EXCEPT) of SELECTs;
  * no node anywhere in the tree that writes, defines, locks, executes or
    changes session state (SELECT … INTO, data-modifying CTEs, FOR UPDATE,
    PRAGMA, ATTACH, COPY, EXEC, SET, …);
  * no comments, wherever the tokenizer attached them;
  * every function call is on an explicit allowlist — anything sqlglot cannot
    map to a known, side-effect-free function (``exp.Anonymous``:
    ``pg_read_file``, ``dblink``, a user-defined function, …) is refused;
  * placeholders only in the plan-dsl form ``%(name)s``; any other parameter
    syntax in the raw template is refused.
"""

from __future__ import annotations

import os
import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

SUPPORTED_DIALECTS: tuple[str, ...] = ("postgres", "duckdb", "tsql", "bigquery", "snowflake")
DEFAULT_DIALECT = "postgres"
DIALECT_ENV = "FONDACO_SQL_DIALECT"

PLACEHOLDER_RE = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
# Stand-in for each %(name)s while parsing. `?` parses as a positional
# placeholder in every supported dialect and can never name a column or table.
_PARSE_PLACEHOLDER = "?"


class SqlRejected(Exception):
    """A template the boundary refuses to reason about. `code` is machine-readable."""

    def __init__(self, code: str, detail: str, relations: tuple[str, ...] = ()) -> None:
        self.code = code
        self.detail = detail
        self.relations = relations
        super().__init__(f"{code}: {detail}")


def _classes(*names: str) -> tuple[type, ...]:
    # getattr keeps the lists robust across sqlglot releases: a class that does
    # not exist in the pinned version simply cannot match.
    return tuple(c for c in (getattr(exp, n, None) for n in names) if isinstance(c, type))


# Any of these anywhere in the tree refuses the template.
_FORBIDDEN_NODES = _classes(
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
    "Into", "Lock", "Copy", "Command", "Pragma", "Attach", "Detach", "Execute",
    "Set", "Use", "Transaction", "Commit", "Rollback", "Grant", "Revoke", "LoadData",
    "Cache", "Uncache", "Describe", "Show", "Kill", "Analyze", "Refresh", "Declare",
    "Summarize", "SessionParameter", "Parameter",
)  # fmt: skip

_ROOT_TYPES = _classes("Select", "Union", "Intersect", "Except")

# Side-effect-free scalar and aggregate functions. Exact-type match, so a
# subclass sqlglot adds later is refused until someone reviews it here.
ALLOWED_FUNCTIONS: frozenset[type] = frozenset(
    _classes(
        # aggregates
        "Count", "Sum", "Avg", "Min", "Max",
        # conditionals / null handling
        "Case", "If", "Coalesce", "Nullif", "Greatest", "Least",
        # casts
        "Cast", "TryCast",
        # strings
        "Lower", "Upper", "Trim", "Length", "Substring", "Concat", "ConcatWs",
        "Left", "Right", "Initcap", "StrPosition",
        # numbers
        "Round", "Abs", "Floor", "Ceil", "Sign",
        # dates and times
        "Extract", "DateTrunc", "TimestampTrunc", "TimeToStr", "StrToDate",
        "StrToTime", "CurrentDate", "CurrentTimestamp", "DateAdd", "DateSub",
        "DateDiff", "TsOrDsToDate", "Date", "Year", "Month", "Day", "DayOfWeek",
        "Quarter",
        # subquery predicate — its subquery is analysed like any other scope
        "Exists",
    )
)  # fmt: skip


def resolve_dialect(dialect: str | None) -> str:
    """The dialect to parse with: explicit argument, else env, else postgres.

    An unknown value is refused, never silently replaced by the default.
    """
    raw = dialect if dialect is not None else os.environ.get(DIALECT_ENV, DEFAULT_DIALECT)
    value = raw.strip().lower() if isinstance(raw, str) else raw
    if value not in SUPPORTED_DIALECTS:
        raise SqlRejected("unsupported_dialect", f"dialect not supported: {raw!r}")
    return value


def parse_template(template: str, dialect: str | None = None) -> exp.Expression:
    """Parse one plan template into a tree, or raise SqlRejected. Never returns None."""
    if not isinstance(template, str):
        raise SqlRejected("sql_parse_error", "template is not a string")
    name = resolve_dialect(dialect)
    n_placeholders = len(PLACEHOLDER_RE.findall(template))
    sql = PLACEHOLDER_RE.sub(_PARSE_PLACEHOLDER, template)
    try:
        statements = sqlglot.parse(sql, read=name)
    except SqlglotError as exc:
        raise SqlRejected("sql_parse_error", f"unparsable as {name}: {type(exc).__name__}") from exc
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected("multi_statement", f"expected one statement, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, _ROOT_TYPES):
        raise SqlRejected("statement_not_select", f"root statement is {type(tree).__name__}")

    placeholders = 0
    for node in tree.walk():
        if isinstance(node, exp.With) and node.args.get("recursive"):
            # plan-dsl v2 §3.1: unbounded iteration is not a read the boundary
            # can reason about or bound; refused outright.
            raise SqlRejected("forbidden_construct", "WITH RECURSIVE is not allowed")
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlRejected("forbidden_construct", f"{type(node).__name__} is not allowed")
        if node.comments:
            raise SqlRejected("comment_forbidden", "SQL comments are forbidden")
        if isinstance(node, exp.Placeholder):
            placeholders += 1
    if placeholders != n_placeholders:
        raise SqlRejected(
            "invalid_placeholder", "parameters must use the %(name)s form and nothing else"
        )

    # Relations before functions: `FROM read_csv(...)` is a read outside the
    # annotated schema (unknown_relation), not merely a disallowed function.
    relations = foreign_relations(tree)
    if relations:
        raise SqlRejected(
            "unknown_relation",
            f"reads outside the annotated schema: {', '.join(relations)}",
            relations=relations,
        )
    for node in tree.walk():
        # AND / OR / XOR are modelled as functions by sqlglot; they are logic,
        # not calls. Every other Func — operator-shaped ones such as `~` or
        # `->` included — must be on the allowlist.
        if isinstance(node, exp.Connector):
            continue
        if isinstance(node, exp.Func) and type(node) not in ALLOWED_FUNCTIONS:
            raise SqlRejected("forbidden_function", f"function not allowed: {_func_name(node)}")
    return tree


def foreign_relations(tree: exp.Expression) -> tuple[str, ...]:
    """Relations that can never be in an annotated schema, whatever it contains.

    * a function in FROM position — ``read_csv(...)``, ``read_parquet(...)``,
      ``customer_emails()``;
    * a table-valued construct — ``VALUES``, ``UNNEST``, ``generate_series``;
      LATERAL is allowed only over a subquery, which is analysed like any other;
    * a schema- or catalog-qualified name — ``pg_catalog.pg_user``,
      ``information_schema.tables``, ``public.customers``,
      ``project.dataset.table``. The annotated schema is a flat namespace, so a
      qualified name is outside it by definition.

    Unknown *bare* names need the schema and are checked by the policy engine.
    """
    found: list[str] = []
    for node in tree.walk():
        if isinstance(node, exp.Table):
            inner = node.this
            if not isinstance(inner, exp.Identifier):
                found.append(f"function:{_relation_func_name(inner)}")
            elif node.args.get("db") or node.args.get("catalog"):
                found.append(node.sql()[:128])
        elif isinstance(node, exp.UDTF):
            if isinstance(node, exp.Lateral) and isinstance(node.this, exp.Subquery):
                continue
            found.append(f"table_function:{type(node).__name__}")
    return tuple(dict.fromkeys(found))


def _relation_func_name(node: object) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name)[:64]
    if isinstance(node, exp.Func):
        return node.sql_name()
    return type(node).__name__


def _func_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name)[:64]
    return node.sql_name()
