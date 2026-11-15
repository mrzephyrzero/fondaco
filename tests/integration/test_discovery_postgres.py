# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Discovery and the annotation overlay against the live demo database."""

import json

from executor.discovery.output import to_json
from executor.discovery.sources import discover_postgres
from tests.integration.conftest import requires_db

pytestmark = requires_db


def test_discover_postgres_proposes_labels_without_leaking_values(seeded_database):
    findings = {t.name: t for t in discover_postgres(seeded_database, max_rows=200)}
    customers = {c.name: c for c in findings["customers"].columns}
    assert customers["email"].label == "restricted"
    assert customers["name"].label == "restricted"
    employees = {c.name: c for c in findings["employees"].columns}
    assert employees["salary"].label == "restricted"
    assert all(t.sampled_rows <= 200 for t in findings.values())
    orders = {c.name: c for c in findings["orders"].columns}
    assert orders["status"].enum_values  # a short closed set of order states
    assert "@" not in to_json(list(findings.values()))


def test_overlay_reaches_the_adapter_schema_only_where_labels_allow(seeded_database, tmp_path):
    from executor.adapters.annotations import load_annotations
    from executor.adapters.postgres import PostgresAdapter

    doc = {"format": "fondaco-annotations", "version": 1, "tables": [
        {"name": "orders", "columns": [
            {"name": "status", "enum_values": ["open", "shipped"],
             "business_description": "order lifecycle"}]},
        {"name": "customers", "columns": [
            {"name": "id", "enum_values": ["1", "2"]}]},
    ]}  # fmt: skip
    path = tmp_path / "a.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    adapter = PostgresAdapter(seeded_database, annotations=load_annotations(path))
    tables = {t.name: t for t in adapter.get_schema().tables}
    status = next(c for c in tables["orders"].columns if c.name == "status")
    assert status.enum_values == ("open", "shipped")
    assert status.label == "internal"
    # customers.id is internal, but the table is restricted: nothing crosses.
    cid = next(c for c in tables["customers"].columns if c.name == "id")
    assert cid.enum_values is None
