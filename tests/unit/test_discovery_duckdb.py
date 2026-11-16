# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""`fondaco discover --duckdb`: local classification of a DuckDB file."""

import json

import pytest

duckdb = pytest.importorskip("duckdb")

from api.cli import main as fondaco  # noqa: E402
from executor.adapters.duckdb_adapter import DuckDBAdapter  # noqa: E402
from executor.adapters.schema_file import load_schema_file  # noqa: E402
from executor.discovery.detectors import is_us_ssn  # noqa: E402
from executor.discovery.sources import DiscoveryError, discover_duckdb  # noqa: E402


@pytest.fixture
def warehouse(tmp_path):
    path = tmp_path / "w.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute(
        "CREATE TABLE employees AS SELECT i AS id, "
        "['Sales','R&D','Ops'][1 + i % 3] AS department, "
        "printf('%03d-%02d-%04d', 100 + i, 10 + i % 80, 1000 + i) AS ssn_code, "
        "50000 + i * 10 AS salary FROM range(600) t(i)"
    )
    conn.close()
    return path


def test_duckdb_source_classifies_and_proposes_enums(warehouse):
    [table] = discover_duckdb(warehouse)
    columns = {c.name: c for c in table.columns}
    assert table.sampled_rows == 500  # capped
    assert columns["ssn_code"].label == "restricted"
    assert any("us_ssn" in r for r in columns["ssn_code"].reasons)
    assert columns["salary"].label == "restricted"  # name hint: salary
    assert columns["department"].enum_values == ("Ops", "R&D", "Sales")
    assert table.label == "internal"  # a floor; columns carry their own labels


def test_duckdb_source_does_not_modify_the_file(warehouse):
    before = warehouse.read_bytes()
    discover_duckdb(warehouse)
    assert warehouse.read_bytes() == before


def test_duckdb_source_missing_file(tmp_path):
    with pytest.raises(DiscoveryError):
        discover_duckdb(tmp_path / "nope.duckdb")


def test_cli_yaml_output_is_a_loadable_labels_file(warehouse, tmp_path, capsys):
    out = tmp_path / "schema.yaml"
    assert (
        fondaco(["discover", "--duckdb", str(warehouse), "--format", "yaml", "--out", str(out)])
        == 0
    )
    labels = load_schema_file(out)
    assert labels["employees"].columns["ssn_code"].label == "restricted"
    # The proposal plugs straight into the adapter (after review).
    schema = DuckDBAdapter(warehouse, labels=labels).get_schema()
    assert {c.name: c.label for c in schema.tables[0].columns}["ssn_code"] == "restricted"


def test_cli_json_never_contains_sampled_pii(warehouse, capsys):
    assert fondaco(["discover", "--duckdb", str(warehouse)]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert "100-10-1000" not in json.dumps(doc)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("123-45-6789", True),
        ("000-12-3456", False),
        ("666-12-3456", False),
        ("912-12-3456", False),
        ("123-00-4567", False),
        ("123-45-0000", False),
        ("123456789", False),
        ("12-345-6789", False),
    ],
)
def test_us_ssn_detector(value, expected):
    assert is_us_ssn(value) is expected
