# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""BigQuery adapter against a running BigQuery emulator, not a fake client.

Opt-in: skipped unless FONDACO_TEST_BQ_EMULATOR names the emulator's REST
endpoint (e.g. http://localhost:9050) and google-cloud-bigquery is
installed. The emulator is goccy/bigquery-emulator, started with
`--project=fondaco-test --dataset=warehouse`; the fixture (re)creates the
tables. It speaks the real REST API, so the real SDK, the real wire format,
parameter binding and result paging are exercised.

What an emulator cannot show, and these tests do not claim: IAM, billing
(`maximum_bytes_billed`) and the server-side `job_timeout_ms`. It also
reports every dry run as `statement_type = SELECT` whatever the statement —
which is exactly why the adapter no longer relies on the dry run alone, and
why the write tests below are meaningful here.
"""

import os
import time

import pytest

from executor.adapters.contract import AdapterError

ENDPOINT = os.environ.get("FONDACO_TEST_BQ_EMULATOR")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="FONDACO_TEST_BQ_EMULATOR not set")

PROJECT = "fondaco-test"
DATASET = "warehouse"
N_NUMBERS = 2_000


def _step(template, params=None):
    return {"id": "s1", "type": "query", "template": template, "params": params or {}}


@pytest.fixture(scope="module")
def client():
    bigquery = pytest.importorskip("google.cloud.bigquery")
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials

    bq_client = bigquery.Client(
        project=PROJECT,
        client_options=ClientOptions(api_endpoint=ENDPOINT),
        credentials=AnonymousCredentials(),
    )
    ds = f"{PROJECT}.{DATASET}"
    field = bigquery.SchemaField

    def table(name, description, fields, rows):
        t = bigquery.Table(f"{ds}.{name}", schema=fields)
        t.description = description
        bq_client.delete_table(t, not_found_ok=True)
        bq_client.create_table(t)
        for start in range(0, len(rows), 500):
            assert bq_client.insert_rows_json(t, rows[start : start + 500]) == []

    table(
        "products",
        "label:public Product catalogue",
        [
            field("id", "INT64", description="label:public"),
            field("name", "STRING", description="label:public"),
            field("category", "STRING", description="label:public"),
        ],
        [
            {"id": 1, "name": "Pallet", "category": "storage"},
            {"id": 2, "name": "Crate", "category": "storage"},
            {"id": 3, "name": "Scanner", "category": "devices"},
        ],
    )
    table(
        "orders",
        "label:internal Orders",
        [
            field("id", "INT64", description="label:internal"),
            field("region", "STRING", description="label:internal"),
            field("order_date", "DATE", description="label:internal"),
            field("amount", "FLOAT64", description="label:confidential Revenue"),
            field("note", "STRING"),  # unlabelled → restricted
        ],
        [
            {"id": 1, "region": "north", "order_date": "2025-05-02", "amount": 120.0},
            {"id": 2, "region": "north", "order_date": "2025-06-10", "amount": 80.5},
            {"id": 3, "region": "south", "order_date": "2025-07-01", "amount": 310.0},
            {"id": 4, "region": "east", "order_date": "2025-08-15", "amount": 42.0},
        ],
    )
    table(
        "numbers",
        "label:public",
        [field("n", "INT64", description="label:public")],
        [{"n": i} for i in range(N_NUMBERS)],
    )
    return bq_client


def _adapter(client, **kwargs):
    from executor.adapters.bigquery import BigQueryAdapter

    return BigQueryAdapter(PROJECT, DATASET, client=client, **kwargs)


def _count(client, table: str) -> int:
    sql = f"SELECT COUNT(*) FROM `{PROJECT}.{DATASET}.{table}`"  # noqa: S608 — test constants
    rows = list(client.query(sql).result())
    return rows[0][0]


# ── Schema and labels ───────────────────────────────────────────────────────


def test_schema_labels_read_from_descriptions(client):
    tables = {t.name: t for t in _adapter(client).get_schema().tables}
    assert {"products", "orders", "numbers"} <= set(tables)
    assert tables["products"].label == "public"
    assert tables["products"].comment == "Product catalogue"
    orders = {c.name: c for c in tables["orders"].columns}
    assert tables["orders"].label == "internal"
    assert orders["amount"].label == "confidential"
    assert orders["note"].label == "restricted"
    assert orders["order_date"].sql_type == "DATE"


def test_execute_binds_typed_params_and_labels(client):
    adapter = _adapter(client)
    result = adapter.execute(
        _step(
            "SELECT region FROM orders WHERE order_date >= %(since)s AND id > %(min_id)s "
            "ORDER BY id",
            {
                "since": {"type": "date", "value": "2025-06-01"},
                "min_id": {"type": "int", "value": 2},
            },
        )
    )
    assert result.columns == ("region",)
    assert result.rows == (("south",), ("east",))
    assert result.label == "internal"
    assert adapter.execute(_step("SELECT amount FROM orders")).label == "confidential"
    assert adapter.execute(_step("SELECT note FROM orders")).label == "restricted"


def test_a_param_is_bound_not_spliced(client):
    result = _adapter(client).execute(
        _step(
            "SELECT id FROM orders WHERE region = %(r)s",
            {"r": {"type": "string", "value": "north' OR '1'='1"}},
        )
    )
    assert result.row_count == 0


# ── Read-only enforcement ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "template",
    [
        "INSERT INTO products (id, name, category) VALUES (90, 'x', 'y')",
        "DELETE FROM products WHERE id = 1",
        "UPDATE products SET name = 'x' WHERE id = 1",
        "CREATE TABLE leak AS SELECT * FROM orders",
        "SELECT 1 AS one; DELETE FROM products WHERE id = 2",
    ],
)
def test_writes_are_refused_even_when_the_dry_run_says_select(client, template):
    # The emulator labels every dry run SELECT, so this only passes because the
    # adapter parses the template itself. Before that check, the INSERT ran.
    before = _count(client, "products")
    with pytest.raises(AdapterError) as excinfo:
        _adapter(client).execute(_step(template))
    assert excinfo.value.kind == "execution"
    assert _count(client, "products") == before == 3


# ── Limits and timeouts ─────────────────────────────────────────────────────


def test_max_rows_is_exact(client):
    assert _adapter(client, max_rows=N_NUMBERS).execute(_step("SELECT n FROM numbers")).row_count
    with pytest.raises(AdapterError) as excinfo:
        _adapter(client, max_rows=N_NUMBERS - 1).execute(_step("SELECT n FROM numbers"))
    assert excinfo.value.kind == "limit_exceeded"


def test_a_slow_query_times_out_within_its_budget(client):
    # 2000^3 = 8·10^9 rows to count: the emulator would run for minutes. The
    # SDK's default retry deadline (600 s) used to keep the adapter waiting
    # far past its one-second budget; now the call gives up on time.
    slow = _adapter(client, statement_timeout_s=1)
    started = time.monotonic()
    with pytest.raises(AdapterError) as excinfo:
        slow.execute(_step("SELECT COUNT(*) AS c FROM numbers AS a, numbers AS b, numbers AS c"))
    elapsed = time.monotonic() - started
    assert excinfo.value.kind == "timeout"
    assert elapsed < 15, f"timed out after {elapsed:.1f}s with a 1s budget"


# ── Errors ──────────────────────────────────────────────────────────────────


def test_driver_text_never_passes_through(client):
    secret = "customer-secret-value"  # noqa: S105 — canary param value, not a credential
    with pytest.raises(AdapterError) as excinfo:
        _adapter(client).execute(
            _step(
                "SELECT missing_column FROM orders WHERE region = %(r)s",
                {"r": {"type": "string", "value": secret}},
            )
        )
    assert excinfo.value.kind == "execution"
    assert secret not in excinfo.value.message
    assert "missing_column" not in excinfo.value.message
