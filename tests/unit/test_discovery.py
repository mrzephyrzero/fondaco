# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Auto-discovery: local, deterministic classification proposals.

Synthetic data only. Every check-digit value is either a published example
or generated here by an independent implementation of the algorithm.
"""

import csv
import json
import random
import string

import pytest

from executor.discovery import detectors as d
from executor.discovery.classifier import MAX_SAMPLE_ROWS, classify_column, classify_table
from executor.discovery.cli import main
from executor.discovery.output import to_dict, to_json, to_sql_comments, to_yaml
from executor.discovery.sources import DiscoveryError, discover_csv, discover_excel

# ── Independent generators ────────────────────────────────────────────────


def _iban(country: str, bban: str) -> str:
    numeric = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{country}{98 - int(numeric) % 97:02d}{bban}"


def _piva(first10: str) -> str:
    odd = sum(int(c) for c in first10[0::2])
    even = sum((2 * int(c)) - 9 if 2 * int(c) > 9 else 2 * int(c) for c in first10[1::2])
    return first10 + str((10 - (odd + even) % 10) % 10)


_ODD = [
    1,
    0,
    5,
    7,
    9,
    13,
    15,
    17,
    19,
    21,
    2,
    4,
    18,
    20,
    11,
    3,
    6,
    8,
    12,
    14,
    16,
    10,
    22,
    25,
    24,
    23,
]


def _cf(first15: str) -> str:
    total = 0
    for i, ch in enumerate(first15):
        idx = int(ch) if ch.isdigit() else ord(ch) - ord("A")
        total += _ODD[idx] if i % 2 == 0 else idx
    return first15 + chr(ord("A") + total % 26)


def _random_cf(rng: random.Random) -> str:
    letters = string.ascii_uppercase
    body = (
        "".join(rng.choice(letters) for _ in range(6))
        + f"{rng.randint(40, 99)}"
        + rng.choice("ABCDEHLMPRST")
        + f"{rng.randint(1, 71):02d}"
        + rng.choice(letters)
        + f"{rng.randint(100, 999)}"
    )
    return _cf(body)


RNG = random.Random(42)  # noqa: S311 — synthetic data
CFS = [_random_cf(RNG) for _ in range(40)]
IBANS = [_iban("IT", f"X0542811101{RNG.randint(10**11, 10**12 - 1)}") for _ in range(40)]
PIVAS = [_piva(f"{RNG.randint(10**9, 10**10 - 1)}") for _ in range(40)]


# ── Detectors ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value", ["RSSMRA85T10A562S", "rssmra85t10a562s", "RSSMRA85T10A56NH"] + CFS[:5]
)
def test_codice_fiscale_valid(value):
    # The third is the omocodia variant of the classic example.
    assert d.is_codice_fiscale(value)


@pytest.mark.parametrize("value", ["RSSMRA85T10A562T", "RSSMRA85T10A562", "ABCDEFGHIJKLMNOP", ""])
def test_codice_fiscale_invalid(value):
    assert not d.is_codice_fiscale(value)


def test_partita_iva():
    assert d.is_partita_iva("12345678903")
    assert d.is_partita_iva("IT12345678903")
    assert all(d.is_partita_iva(p) for p in PIVAS)
    assert not d.is_partita_iva("12345678904")
    assert not d.is_partita_iva("00000000000")
    assert not d.is_partita_iva("1234567890")


def test_iban():
    for value in (
        "IT60X0542811101000000123456",
        "IT60 X054 2811 1010 0000 0123 456",
        "GB82WEST12345698765432",
        "DE89370400440532013000",
        *IBANS,
    ):
        assert d.is_iban(value), value
    assert not d.is_iban("IT61X0542811101000000123456")
    assert not d.is_iban("IT60X05428")


def test_email_phone_ip():
    assert d.is_email("mario.rossi@example.it")
    assert not d.is_email("mario.rossi@")
    for phone in ("+39 333 123 4567", "0039 02 1234567", "3331234567", "02/12345678", "06 1234567"):
        assert d.is_phone(phone), phone
    for not_phone in ("2024-01-15", "15/01/2024", "1234567.89", "12345678", "99999999999", "42"):
        assert not d.is_phone(not_phone), not_phone
    assert d.is_ip_address("10.0.0.1")
    assert d.is_ip_address("2001:db8::1")
    assert not d.is_ip_address("10.0.0")


@pytest.mark.parametrize(
    ("name", "label", "token"),
    [
        ("stipendio", "restricted", "stipendio"),
        ("RAL", "restricted", "ral"),
        ("cliente_cf", "restricted", "cf"),
        ("ibanFornitore", "restricted", "iban"),
        ("margine_lordo", "confidential", "margine"),
        ("unit_price", "confidential", "price"),
        ("PrezzoUnitario", "confidential", "prezzo"),
        ("price_cfg", "confidential", "price"),  # `cfg` is not `cf`
        ("status", None, None),
        ("viale", None, None),  # `via` is a token, never a substring
    ],
)
def test_name_hints_match_whole_tokens(name, label, token):
    assert d.name_hint(name) == (label, token)


# ── Classifier ────────────────────────────────────────────────────────────


def test_value_detector_wins_over_innocent_name():
    finding = classify_column("note_cliente", "text", CFS[:20] + ["boh"] * 5)
    assert finding.label == "restricted"
    assert finding.reasons[0].startswith("values:codice_fiscale:20/25")
    assert finding.enum_values is None


def test_a_couple_of_stray_matches_do_not_flip_a_column():
    values = [f"order note {i}" for i in range(98)] + CFS[:1]
    assert classify_column("note", "text", values).label == "internal"


def test_small_all_matching_sample_is_restricted():
    assert classify_column("x", "text", [IBANS[0]]).label == "restricted"


def test_name_raises_label_and_restricted_name_beats_confidential():
    assert classify_column("ral", "numeric", [32000, 41000]).label == "restricted"
    assert classify_column("prezzo", "numeric", ["1.50", "2.00"]).label == "confidential"
    assert classify_column("margin_iban", "text", []).label == "restricted"


def test_enum_proposed_for_repeated_short_codes():
    finding = classify_column("stato", "text", ["01", "02", "03"] * 10 + [None] * 3)
    assert finding.label == "internal"
    assert finding.enum_values == ("01", "02", "03")
    assert finding.sampled == 30
    assert finding.distinct == 3


@pytest.mark.parametrize(
    "values",
    [
        ["a", "b", "c"],  # few values but never repeated: identifiers, not codes
        [f"S{i}" for i in range(31)] * 2,  # > 30 distinct
        ["x" * 65] * 2,  # too long
        [],
    ],
)
def test_enum_not_proposed(values):
    assert classify_column("stato", "text", values).enum_values is None


def test_enum_never_on_confidential_or_restricted_columns():
    assert classify_column("sconto", "text", ["10", "20"] * 10).enum_values is None
    assert classify_column("cap", "text", ["00100", "20121"] * 10).enum_values is None


def test_dates_and_amounts_stay_internal():
    dates = [f"2024-{m:02d}-{day:02d}" for m in range(1, 13) for day in range(1, 28)]
    assert classify_column("data_ordine", "date", dates).label == "internal"
    amounts = [f"{RNG.randint(10**6, 10**8)}.{RNG.randint(10, 99)}" for _ in range(100)]
    assert classify_column("importo", "numeric", amounts).label == "internal"


def test_binary_is_never_inspected():
    finding = classify_column("blob", "bytea", [b"RSSMRA85T10A562S"] * 4)
    assert finding.label == "internal"
    assert finding.sampled == 0


def test_classify_table_caps_sample_and_never_proposes_public():
    rows = [("01", "x")] * (MAX_SAMPLE_ROWS + 100)
    table = classify_table("t", [("code", "text"), ("free", "text")], rows)
    assert table.sampled_rows == MAX_SAMPLE_ROWS
    assert table.label == "internal"
    assert all(c.label != "public" for c in table.columns)
    empty = classify_table("t", [("email", "text")], [])
    assert empty.notes and empty.columns[0].label == "restricted"


# ── Sources ───────────────────────────────────────────────────────────────


def _write_dataset(path, n=60, delimiter=";"):
    rng = random.Random(7)  # noqa: S311 — synthetic data
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=delimiter)
        w.writerow(
            [
                "id", "descrizione", "codice_cliente", "conto", "fornitore_vat", "contatto",
                "stipendio", "margine", "stato", "note",
            ]
        )  # fmt: skip
        for i in range(n):
            w.writerow(
                [
                    i + 1,
                    f"Articolo generico {i}",
                    CFS[i % len(CFS)],
                    IBANS[i % len(IBANS)],
                    PIVAS[i % len(PIVAS)],
                    f"utente{i}@example.it",
                    rng.randint(25000, 60000),
                    f"{rng.uniform(0, 40):.2f}",
                    rng.choice(["APERTO", "CHIUSO", "SOSPESO"]),
                    f"nota libera numero {i}",
                ]
            )


@pytest.fixture
def dataset(tmp_path):
    path = tmp_path / "anagrafica.csv"
    _write_dataset(path)
    return path


def test_csv_discovery_classifies_synthetic_dataset(dataset):
    (table,) = discover_csv(dataset)
    labels = {c.name: c.label for c in table.columns}
    assert table.name == "anagrafica"
    assert table.sampled_rows == 60
    assert labels == {
        "id": "internal",
        "descrizione": "internal",
        "codice_cliente": "restricted",  # values: codice fiscale
        "conto": "restricted",  # values: IBAN
        "fornitore_vat": "restricted",  # values + name
        "contatto": "restricted",  # values: email
        "stipendio": "restricted",  # name: payroll
        "margine": "confidential",
        "stato": "internal",
        "note": "internal",
    }
    by_name = {c.name: c for c in table.columns}
    assert by_name["stato"].enum_values == ("APERTO", "CHIUSO", "SOSPESO")
    assert by_name["note"].enum_values is None
    assert by_name["id"].enum_values is None


def test_proposal_never_contains_sensitive_sample_values(dataset):
    findings = discover_csv(dataset)
    rendered = to_json(findings) + to_yaml(findings) + to_sql_comments(findings)
    for value in CFS + IBANS + PIVAS:
        assert value not in rendered
    assert "@example.it" not in rendered
    assert "Articolo generico" not in rendered
    assert "nota libera" not in rendered


def test_csv_sampling_is_bounded_and_reproducible(tmp_path):
    path = tmp_path / "big.csv"
    _write_dataset(path, n=2000, delimiter=",")
    first = discover_csv(path, max_rows=100)
    assert first[0].sampled_rows == 100
    assert to_dict(first) == to_dict(discover_csv(path, max_rows=100))
    assert discover_csv(path, max_rows=10_000)[0].sampled_rows == MAX_SAMPLE_ROWS


def test_csv_errors_are_discovery_errors(tmp_path):
    with pytest.raises(DiscoveryError):
        discover_csv(tmp_path / "missing.csv")
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(DiscoveryError):
        discover_csv(empty)


def test_excel_discovery(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "dipendenti"
    sheet.append(["matricola", "codice_fiscale", "ral", "reparto"])
    for i in range(30):
        sheet.append([i, CFS[i], 30000 + i, ["MAG", "UFF"][i % 2]])
    path = tmp_path / "hr.xlsx"
    workbook.save(path)
    (table,) = discover_excel(path)
    labels = {c.name: c.label for c in table.columns}
    assert labels == {
        "matricola": "internal",
        "codice_fiscale": "restricted",
        "ral": "restricted",
        "reparto": "internal",
    }
    assert table.columns[3].enum_values == ("MAG", "UFF")


# ── Output ────────────────────────────────────────────────────────────────


def test_json_shape_requires_review(dataset):
    doc = json.loads(to_json(discover_csv(dataset)))
    assert doc["format"] == "fondaco-annotations"
    assert doc["review_required"] is True
    columns = {c["name"]: c for c in doc["tables"][0]["columns"]}
    assert columns["stato"]["enum_values"] == ["APERTO", "CHIUSO", "SOSPESO"]
    assert columns["conto"]["enum_values"] is None


def test_yaml_is_loadable_when_a_yaml_parser_is_available(dataset):
    yaml = pytest.importorskip("yaml")
    findings = discover_csv(dataset)
    assert yaml.safe_load(to_yaml(findings)) == to_dict(findings)


def test_sql_comments_quote_hostile_names():
    table = classify_table('we"ird', [("o'brien", "text")], [])
    sql = to_sql_comments([table], schema="s")
    assert 'COMMENT ON TABLE "s"."we""ird" IS \'label:internal (discovered)\';' in sql
    assert 'COMMENT ON COLUMN "s"."we""ird"."o\'brien" IS' in sql
    assert sql.splitlines()[2] == "BEGIN;" and sql.rstrip().endswith("COMMIT;")


# ── CLI ───────────────────────────────────────────────────────────────────


def test_cli_round_trip(dataset, tmp_path, capsys):
    out = tmp_path / "proposal.yaml"
    sql = tmp_path / "comments.sql"
    assert main(["discover", "--csv", str(dataset), "--table", "clienti", "--format", "yaml",
                 "--out", str(out), "--sql-comments", str(sql)]) == 0  # fmt: skip
    assert '"name": "clienti"' in out.read_text(encoding="utf-8")
    assert 'COMMENT ON COLUMN "public"."clienti"."stipendio"' in sql.read_text(encoding="utf-8")
    assert main(["discover", "--csv", str(dataset)]) == 0
    assert json.loads(capsys.readouterr().out)["tables"][0]["name"] == "anagrafica"


def test_cli_refuses_oversized_sample_and_reports_errors(tmp_path, capsys):
    assert main(["discover", "--csv", "x.csv", "--max-rows", "501"]) == 2
    assert main(["discover", "--csv", str(tmp_path / "missing.csv")]) == 1
    assert "discovery failed" in capsys.readouterr().err
