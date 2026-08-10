# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Frozen behaviour of the label scanner across SQL constructs (audit §3.2).

These tests do not close the bug class. A syntactic scanner over SQL text
cannot be made complete, and nothing here makes it complete. What they do is
freeze the *current* outcome for six constructs that were verified by hand but
carried no test, so that an edit to the regexes in boundary/policy.py fails
loudly instead of regressing silently.

Every construct below reaches RESTRICTED. Two different mechanisms produce
that answer, and the difference is the whole point of this file:

  DETECTION — the restricted table name was captured by the FROM/JOIN scan and
      resolved against schema_labels. The label is right *because the scanner
      understood the query*. It stays right as the schema grows.

  IGNORANCE — the scanner never resolved the reference. It either aborted on a
      construct it cannot parse (`_referenced_tables` → None) or captured a
      name that is absent from schema_labels, and fell back to RESTRICTED
      either way. The label is right *by accident of failing closed*; the
      restricted table itself was never identified. This is contingent on the
      name staying unknown — see `test_view_over_restricted_table` and the
      inversion note attached to it.

Each test asserts the pair (detected, unresolved) via `_why`, so the mechanism
is machine-checked rather than merely asserted in prose. Some constructs are
over-determined and trip both; those record both.
"""

from boundary.policy import Label, _referenced_tables, query_label

LABELS = {
    "orders": {
        "label": "internal",
        "columns": {"region": "internal", "customer_id": "internal"},
    },
    "customers": {
        "label": "restricted",
        "columns": {"email": "restricted", "name": "restricted"},
    },
    "products": {"label": "public", "columns": {"category": "public"}},
}

RESTRICTED_TABLE = "customers"


def _why(template: str) -> tuple[bool, bool]:
    """(detected, unresolved) — which mechanism(s) drove the RESTRICTED label.

    `detected` is True when the restricted table name reached the schema
    lookup. `unresolved` is True when the scan aborted, or captured at least
    one name that schema_labels does not know. Deliberately reads the private
    `_referenced_tables`: the distinction is invisible from query_label's
    return value, and coupling to the scanner internals is what makes an edit
    to those regexes fail here.
    """
    refs = _referenced_tables(template)
    if refs is None or not refs:
        return (False, True)
    return (RESTRICTED_TABLE in refs, any(name not in LABELS for name in refs))


# ── DETECTION — the restricted table is seen and resolved ──────────────────


def test_cross_join_detection():
    # `CROSS JOIN customers` — the JOIN keyword still precedes the table name,
    # so the scan captures it like any explicit join.
    t = "SELECT c.email FROM orders o CROSS JOIN customers c ON true"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (True, False)


def test_join_using_detection():
    # `JOIN customers USING (...)` — USING replaces ON, the table name is
    # unaffected, so this is detection just like an ON-join.
    t = "SELECT email FROM orders JOIN customers USING (customer_id)"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (True, False)


# ── DETECTION and IGNORANCE together — over-determined outcomes ────────────


def test_lateral_join_detection_and_ignorance():
    # Over-determined. The scan reads LATERAL as if it were a table name
    # (`JOIN LATERAL` → capture "LATERAL", unknown → restricted), *and*
    # separately captures `customers` from the subquery's own FROM. Either one
    # alone yields RESTRICTED here; removing the LATERAL-as-table accident
    # would not change the verdict for this shape.
    t = (
        "SELECT x.email FROM orders o CROSS JOIN LATERAL "
        "(SELECT email FROM customers WHERE id = o.customer_id) x"
    )
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (True, True)


def test_set_returning_function_with_restricted_subquery_argument():
    # Over-determined. `FROM unnest(...)` captures "unnest" as a table name
    # (unknown → restricted); the restricted table inside the SRF argument is
    # also captured by its own FROM. Contrast with the opaque-SRF case below,
    # which is the shape audit §3.2 describes: there the restricted table
    # never appears in the query text at all.
    t = "SELECT e FROM unnest((SELECT array_agg(email) FROM customers)) AS e"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (True, True)


# ── IGNORANCE — the restricted table is never identified ───────────────────


def test_set_returning_function_ignorance():
    # A set-returning function that reads the restricted table in its own body.
    # This is the shape audit §3.2 has in mind: the restricted table is reached
    # *through* the construct and its name never appears in the query text, so
    # there is nothing for the scan to detect. Only the unknown function name
    # stands between this query and the data. Nothing in the boundary resolves
    # a function to the tables it reads — the same blind spot as views.
    t = "SELECT email FROM customer_emails()"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (False, True)


def test_lateral_after_comma_ignorance():
    # The comma-join guard fires before any name resolution: `_referenced_
    # tables` returns None and the restricted table is never looked at. Pure
    # fail-closed. Contrast with test_lateral_join_detection_and_ignorance,
    # where the same LATERAL subquery *is* seen.
    t = "SELECT x.email FROM orders o, LATERAL (SELECT email FROM customers) x"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (False, True)


def test_derived_table_in_from_ignorance():
    # `FROM (` — the capture group does not match an open paren, so the scan
    # aborts on the first reference and returns None. The inner `FROM
    # customers` is never reached. Pure fail-closed: RESTRICTED here says
    # "I could not read this query", not "this query reads customers".
    t = "SELECT t.email FROM (SELECT email FROM customers) t"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (False, True)


def test_view_over_restricted_table():
    # A view over a restricted base table. The view name is captured cleanly,
    # but schema_labels has no entry for it — get_schema filters to ordinary
    # tables (`relkind = 'r'`, executor/adapters/postgres.py) so a view can
    # never appear there — and the unknown-table branch fails closed. The base
    # table is never resolved; views are not expanded.
    #
    # INVERSION RISK (audit §3.2, "The VIEW case"): the protection is a side effect of the view
    # being unknown, not of anything the scanner understands about views. Two
    # consequences, both current and both intended to be read together:
    #   1. Every view is unusable — legitimate views over public data are
    #      denied for exactly the same reason.
    #   2. If views were ever added to schema_labels to fix (1), the label
    #      attached to the view entry would be believed. A view labelled
    #      `internal` over a `restricted` base table would return INTERNAL and
    #      the restricted data would cross the boundary. The protection does
    #      not weaken — it inverts. Adding views to schema_labels requires
    #      resolving views to their base tables first.
    t = "SELECT email FROM v_customer_emails"
    assert query_label(t, LABELS) == Label.RESTRICTED
    assert _why(t) == (False, True)


def test_view_protection_inverts_if_view_is_labeled():
    # Demonstration of consequence (2) above, not desired behaviour. Nothing in
    # the boundary connects `v_customer_emails` to `customers`; labelling the
    # view is taken at face value. This test exists so the inversion is a
    # recorded, executable fact rather than a paragraph someone has to trust.
    labels_with_view = {
        **LABELS,
        "v_customer_emails": {"label": "internal", "columns": {"email": "internal"}},
    }
    t = "SELECT email FROM v_customer_emails"
    assert query_label(t, labels_with_view) == Label.INTERNAL
