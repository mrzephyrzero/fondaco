# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""The reviewed discovery overlay: enum metadata only, never labels."""

import json

import pytest

from executor.adapters.annotations import (
    ANNOTATIONS_ENV,
    AnnotationsError,
    annotations_from_env,
    apply_annotations,
    load_annotations,
)
from executor.adapters.contract import Column, Table


def _doc(columns, table="orders"):
    return {"format": "fondaco-annotations", "version": 1, "tables": [
        {"name": table, "label": "internal", "columns": columns}]}  # fmt: skip


def _write(tmp_path, doc):
    path = tmp_path / "annotations.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _orders(table_label="internal", status_label="internal"):
    return Table(
        name="orders",
        label=table_label,
        columns=(
            Column("status", "text", status_label),
            Column("email", "text", "restricted"),
        ),
    )


STATUS = {"name": "status", "label": "public", "enum_values": ["01", "02"],
          "business_description": "01=Open, 02=Shipped"}  # fmt: skip


def test_overlay_adds_metadata_on_internal_column(tmp_path):
    annotations = load_annotations(_write(tmp_path, _doc([STATUS])))
    table = apply_annotations(_orders(), annotations)
    status = table.columns[0]
    assert status.enum_values == ("01", "02")
    assert status.business_description == "01=Open, 02=Shipped"
    assert status.label == "internal"  # the file said "public": ignored


@pytest.mark.parametrize(("table_label", "status_label"),
                         [("confidential", "internal"), ("internal", "restricted"),
                          ("internal", "bogus")])  # fmt: skip
def test_overlay_never_reaches_a_column_above_internal(tmp_path, table_label, status_label):
    annotations = load_annotations(_write(tmp_path, _doc([STATUS])))
    table = apply_annotations(_orders(table_label, status_label), annotations)
    assert table.columns[0].enum_values is None
    assert table.columns[0].business_description is None


def test_overlay_on_restricted_column_is_dropped(tmp_path):
    hostile = {"name": "email", "enum_values": ["a@x.it", "b@x.it"]}
    table = apply_annotations(_orders(), load_annotations(_write(tmp_path, _doc([hostile]))))
    assert table.columns[1].enum_values is None


@pytest.mark.parametrize("bad", [
    {"enum_values": [str(i) for i in range(31)]},
    {"enum_values": ["x" * 65]},
    {"enum_values": "01,02"},
    {"enum_values": [{"nested": 1}]},
    {"enum_values": ["a", "a"]},
    {"business_description": 42},
])  # fmt: skip
def test_malformed_metadata_is_dropped_for_that_column_only(tmp_path, bad):
    doc = _doc([{"name": "status", **bad}])
    table = apply_annotations(_orders(), load_annotations(_write(tmp_path, doc)))
    assert table == _orders()


def test_other_tables_and_unknown_columns_are_untouched(tmp_path):
    annotations = load_annotations(_write(tmp_path, _doc([STATUS], table="deliveries")))
    assert apply_annotations(_orders(), annotations) == _orders()


@pytest.mark.parametrize("content", ["not json", json.dumps({"format": "other"}),
                                     json.dumps({"format": "fondaco-annotations"})])  # fmt: skip
def test_unreadable_files_raise(tmp_path, content):
    path = tmp_path / "a.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AnnotationsError):
        load_annotations(path)
    with pytest.raises(AnnotationsError):
        load_annotations(tmp_path / "missing.json")


def test_env_loading(tmp_path):
    assert annotations_from_env({}) == {}
    assert annotations_from_env({ANNOTATIONS_ENV: "  "}) == {}
    path = _write(tmp_path, _doc([STATUS]))
    assert ("orders", "status") in annotations_from_env({ANNOTATIONS_ENV: str(path)})


def test_discovery_output_round_trips_into_the_overlay(tmp_path):
    from executor.discovery.classifier import classify_table
    from executor.discovery.output import to_json

    finding = classify_table("orders", [("status", "text"), ("email", "text")],
                             [("01", "a@x.it"), ("02", "b@x.it")] * 3)  # fmt: skip
    path = tmp_path / "proposal.json"
    path.write_text(to_json([finding]), encoding="utf-8")
    table = apply_annotations(_orders(), load_annotations(path))
    assert table.columns[0].enum_values == ("01", "02")
    assert table.columns[1].enum_values is None
