# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""BigQuery adapter against a fake client: dry-run gate, cost/time caps, labels, errors."""

from types import SimpleNamespace

import pytest

from executor.adapters.bigquery import BigQueryAdapter
from executor.adapters.contract import AdapterError


class FakeBQ:
    """Stand-in for `google.cloud.bigquery`: records the job configs it builds."""

    class QueryJobConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ScalarQueryParameter:
        def __init__(self, name, type_, value):
            self.name, self.type_, self.value = name, type_, value


def _field(name, field_type, description):
    return SimpleNamespace(name=name, field_type=field_type, description=description)


TABLES = {
    "orders": SimpleNamespace(
        table_id="orders",
        table_type="TABLE",
        description="label:internal Orders",
        num_rows=1200,
        schema=[
            _field("region", "STRING", "label:internal"),
            _field("margin", "NUMERIC", "label:confidential"),
            _field("email", "STRING", None),
        ],
    ),
    "orders_view": SimpleNamespace(
        table_id="orders_view",
        table_type="VIEW",
        description="label:public",
        num_rows=0,
        schema=[],
    ),  # fmt: skip
}


class Row:
    def __init__(self, values):
        self._values = values

    def values(self):
        return self._values


class FakeResult(list):
    schema = [SimpleNamespace(name="region"), SimpleNamespace(name="n")]


class FakeClient:
    def __init__(self, statement_type="SELECT", rows=(("north", 7), ("south", 6)), error=None):
        self.statement_type = statement_type
        self.rows = rows
        self.error = error
        self.queries = []
        self.query_kwargs = []
        self.result_kwargs = None
        self.cancelled = False

    def list_tables(self, dataset_id, timeout=None):
        self.dataset_id = dataset_id
        return [SimpleNamespace(reference=name) for name in TABLES]

    def get_table(self, reference, timeout=None):
        return TABLES[reference]

    def query(self, sql, job_config=None, **kwargs):
        self.queries.append((sql, job_config))
        self.query_kwargs.append(kwargs)
        if job_config.dry_run:
            return SimpleNamespace(statement_type=self.statement_type)
        client = self

        class Job:
            def result(self, **kwargs):
                client.result_kwargs = kwargs
                if client.error is not None:
                    raise client.error
                return FakeResult(Row(r) for r in client.rows[: kwargs["max_results"]])

            def cancel(self, **kwargs):
                client.cancelled = True

        return Job()


def _adapter(client=None, **kwargs):
    client = client or FakeClient()
    adapter = BigQueryAdapter(
        "acme-analytics", "erp", client=client, bigquery_module=FakeBQ, **kwargs
    )
    return adapter, client


def _step(template="SELECT region, count(*) AS n FROM orders GROUP BY region", params=None):
    return {"id": "s1", "type": "query", "template": template, "params": params or {}}


def test_schema_labels_from_descriptions_views_excluded():
    adapter, client = _adapter()
    schema = adapter.get_schema()
    assert client.dataset_id == "acme-analytics.erp"
    assert [t.name for t in schema.tables] == ["orders"]
    cols = {c.name: c.label for c in schema.tables[0].columns}
    assert cols == {"region": "internal", "margin": "confidential", "email": "restricted"}
    assert schema.tables[0].row_count == 1200


def test_dry_run_gate_then_capped_execution():
    adapter, client = _adapter(maximum_bytes_billed=5_000_000, statement_timeout_s=20)
    result = adapter.execute(
        _step(
            "SELECT region, count(*) AS n FROM orders WHERE region <> %(r)s GROUP BY region",
            {"r": {"type": "string", "value": "x"}},
        )
    )
    (dry_sql, dry_cfg), (sql, cfg) = client.queries
    assert dry_cfg.dry_run is True and cfg.dry_run is False
    assert sql == dry_sql and "@r" in sql and "%(" not in sql
    assert cfg.maximum_bytes_billed == 5_000_000
    assert cfg.job_timeout_ms == 20_000
    assert cfg.default_dataset == "acme-analytics.erp"
    assert cfg.use_legacy_sql is False
    (param,) = cfg.query_parameters
    assert (param.name, param.type_, param.value) == ("r", "STRING", "x")
    # Every API call is bounded by the statement timeout, retries included.
    assert client.result_kwargs == {"max_results": 10_001, "timeout": 20, "retry": None}
    assert client.query_kwargs == [{"timeout": 20, "retry": None, "job_retry": None}] * 2
    assert result.rows == (("north", 7), ("south", 6)) and result.label == "internal"


@pytest.mark.parametrize(
    "template",
    [
        "INSERT INTO orders (region) VALUES ('x')",
        "DELETE FROM orders WHERE region = 'x'",
        "SELECT region FROM orders; DELETE FROM orders WHERE region = 'x'",
        "CREATE TABLE leak AS SELECT region FROM orders",
    ],
)
def test_non_select_is_refused_before_any_request_even_if_dry_run_says_select(template):
    # The BigQuery emulator reports every dry run as SELECT; a write got
    # through when the dry run was the only gate (test_bigquery_live.py).
    adapter, client = _adapter(FakeClient(statement_type="SELECT"))
    with pytest.raises(AdapterError) as err:
        adapter.execute(_step(template))
    assert err.value.kind == "execution"
    assert client.queries == []


def test_retry_deadline_is_the_statement_timeout():
    class Retry:
        def __init__(self, timeout=600.0):
            self.timeout = timeout

        def with_timeout(self, timeout):
            return Retry(timeout)

    class BQWithRetry(FakeBQ):
        DEFAULT_RETRY = Retry()

    client = FakeClient()
    adapter = BigQueryAdapter(
        "acme-analytics", "erp", client=client, bigquery_module=BQWithRetry, statement_timeout_s=7
    )
    adapter.execute(_step())
    assert {kw["retry"].timeout for kw in client.query_kwargs} == {7}
    assert client.result_kwargs["retry"].timeout == 7


@pytest.mark.parametrize("statement_type", ["INSERT", "DELETE", "SCRIPT", "CREATE_TABLE", None])
def test_anything_but_select_is_refused_after_dry_run(statement_type):
    adapter, client = _adapter(FakeClient(statement_type=statement_type))
    with pytest.raises(AdapterError):
        adapter.execute(_step())
    assert len(client.queries) == 1  # only the dry run ever reached BigQuery


def test_column_precise_label_in_bigquery_dialect():
    adapter, _ = _adapter()
    assert adapter.execute(_step("SELECT region FROM orders WHERE margin > 0")).label == (
        "confidential"
    )
    assert adapter.execute(_step("SELECT email FROM orders")).label == "restricted"


def test_row_overflow_errors_rather_than_truncates():
    adapter, _ = _adapter(FakeClient(rows=[("a", 1)] * 4), max_rows=3)
    with pytest.raises(AdapterError) as err:
        adapter.execute(_step())
    assert err.value.kind == "limit_exceeded"


class DeadlineExceeded(Exception):
    code = 504


class BadRequest(Exception):
    code = 400


class RetryError(Exception):
    """google-api-core's form of 'the retry deadline ran out'."""


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (DeadlineExceeded("deadline"), "timeout"),
        (RetryError("Timeout of 1.0s exceeded"), "timeout"),
        (BadRequest("Bad int64 value: alice@example.it"), "execution"),
    ],
)
def test_errors_are_sanitized(error, kind):
    adapter, client = _adapter(FakeClient(error=error))
    with pytest.raises(AdapterError) as err:
        adapter.execute(_step())
    assert err.value.kind == kind
    assert "alice" not in str(err.value)
    assert client.cancelled == (kind == "timeout")  # a timed-out job is not left running


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project": "acme-analytics", "dataset": "erp.other"},
        {"project": "Bad Project", "dataset": "erp"},
        {"project": "acme-analytics", "dataset": "erp", "maximum_bytes_billed": 0},
    ],
)
def test_configuration_is_validated(kwargs):
    with pytest.raises(AdapterError):
        BigQueryAdapter(client=FakeClient(), bigquery_module=FakeBQ, **kwargs)


def test_undeclared_param_refused_before_any_query():
    adapter, client = _adapter()
    with pytest.raises(AdapterError):
        adapter.execute(_step("SELECT region FROM orders WHERE region = %(nope)s"))
    assert client.queries == []


def test_missing_sdk_is_a_connection_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_bq(name, *args, **kwargs):
        if name.startswith("google"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_bq)
    with pytest.raises(AdapterError) as err:
        BigQueryAdapter("acme-analytics", "erp").get_schema()
    assert "fondaco[bigquery]" in str(err.value)
