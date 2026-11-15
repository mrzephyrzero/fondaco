# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Behaviour of the label analyser across SQL constructs (audit §3.2).

These constructs were first pinned against the regex FROM/JOIN scanner, where
most of them reached RESTRICTED by IGNORANCE: the scanner aborted or captured
a name it did not know and failed closed, without ever identifying the
restricted table. The scanner has been replaced by a syntax-tree analyser
(boundary/sql_ast.py + boundary/policy.py), and the same constructs now land
in one of three explicitly distinguished outcomes:

  DETECTION — the restricted column was resolved, through whatever aliases,
      joins and subqueries stand in the way, to `customers`. The label is right
      *because the analyser understood the query*.

  UNKNOWN RELATION — the template reads something that is not a relation of
      the annotated schema (a table function, a qualified name, an unknown
      name). This is an absolute deny, not a RESTRICTED label: no clearance
      admits it.

  UNRESOLVED — the template parsed and every relation is known, but some
      reference could not be resolved (unknown column, whole-row reference).
      RESTRICTED by fail-closed default, per label-model.md §4.

Each test asserts the mechanism through `_why`, so a change in *why* a
construct is denied fails loudly even when the verdict stays the same.
"""

from boundary.policy import Label, analyze_query, query_label

LABELS = {
    "orders": {
        "label": "internal",
        "columns": {"id": "internal", "region": "internal", "customer_id": "internal"},
    },
    "customers": {
        "label": "restricted",
        "columns": {"id": "restricted", "email": "restricted", "name": "restricted"},
    },
    "products": {"label": "public", "columns": {"category": "public"}},
}

RESTRICTED_TABLE = "customers"


def _why(template: str) -> str:
    analysis = analyze_query(template, LABELS)
    if analysis.unknown_relations:
        return "unknown_relation"
    if not analysis.resolved:
        return "unresolved"
    assert RESTRICTED_TABLE in analysis.tables
    return "detected"


# ── DETECTION ─────────────────────────────────────────────────────────────


def test_cross_join_detection():
    t = "SELECT c.email FROM orders o CROSS JOIN customers c ON true"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "detected"


def test_join_using_detection():
    t = "SELECT email FROM orders JOIN customers USING (id)"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "detected"


def test_lateral_join_detection():
    # Under the regex scanner LATERAL was captured as if it were a table name.
    # Now the LATERAL subquery is a scope like any other, and its correlated
    # reference to the outer `o` resolves through the enclosing scope.
    t = (
        "SELECT x.email FROM orders o CROSS JOIN LATERAL "
        "(SELECT email FROM customers WHERE id = o.customer_id) x"
    )
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "detected"


def test_lateral_after_comma_detection():
    # Was pure fail-closed (the comma-join guard fired before any resolution).
    # The comma is now parsed as the join it is.
    t = "SELECT x.email FROM orders o, LATERAL (SELECT email FROM customers) x"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "detected"


def test_derived_table_in_from_detection():
    # Was aborted on `FROM (`. Now `t.email` resolves to customers.email.
    t = "SELECT t.email FROM (SELECT email FROM customers) t"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "detected"


# ── UNKNOWN RELATION — absolute deny ─────────────────────────────────────


def test_set_returning_function_with_restricted_subquery_argument():
    # `unnest(...)` in FROM is a table-valued construct, outside the schema.
    t = "SELECT e FROM unnest((SELECT array_agg(email) FROM customers)) AS e"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "unknown_relation"


def test_set_returning_function_is_an_unknown_relation():
    # The shape audit §3.2 has in mind: a function that reads the restricted
    # table in its own body, so the table never appears in the query text.
    # Nothing resolves a function to the tables it reads; it is refused as a
    # relation outside the annotated schema.
    t = "SELECT email FROM customer_emails()"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "unknown_relation"


def test_view_over_restricted_table():
    # Views are never in the schema labels (get_schema reads relkind = 'r'),
    # so a view name is an unknown relation and is denied absolutely.
    t = "SELECT email FROM v_customer_emails"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "unknown_relation"


def test_view_protection_inverts_if_view_is_labeled():
    # INVERSION RISK (audit §3.2, "The VIEW case") — unchanged by the AST
    # analyser, and recorded here so it stays an executable fact. Nothing in
    # the boundary connects `v_customer_emails` to `customers`; a label put on
    # the view is taken at face value. Adding views to the schema labels
    # requires resolving them to their base tables first.
    labels_with_view = {
        **LABELS,
        "v_customer_emails": {"label": "internal", "columns": {"email": "internal"}},
    }
    t = "SELECT email FROM v_customer_emails"
    assert query_label(t, labels_with_view) == Label.INTERNAL


# ── UNRESOLVED — restricted by default ──────────────────────────────────


def test_whole_row_reference_is_unresolved():
    # `SELECT c FROM customers c` returns every column without naming one.
    t = "SELECT o FROM orders o"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "unresolved"


def test_unknown_column_is_unresolved():
    t = "SELECT ssn FROM orders"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == "unresolved"
