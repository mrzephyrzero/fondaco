# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Column classification for auto-discovery: a *proposal* for the Data Owner.

Order of precedence, most sensitive first, and the first rule that fires wins:

  1. value detectors — a share of the sampled non-null values passes a
     check-digit-validated PII detector (fiscal code, VAT, IBAN, email,
     phone, IP) → `restricted`;
  2. column-name hints for PII, payroll, credentials → `restricted`;
  3. column-name hints for commercial data (margin, cost, price, discount,
     revenue) → `confidential`;
  4. anything else → `internal`.

`public` is never proposed: publishing is a human decision, and discovery
only ever errs upward. For an `internal` column whose sample shows at most
`ENUM_MAX_VALUES` distinct short values, each seen at least twice, the
values are proposed as `enum_values` (adapter-contract v1 §2.1). The
repetition rule keeps free text and identifiers — which happen to be few in
a small sample — out of the enum.

Findings never contain sample values except proposed `enum_values`, and the
sample itself is dropped by the caller as soon as classification ends.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from executor.adapters.contract import ENUM_MAX_VALUE_LENGTH, ENUM_MAX_VALUES
from executor.discovery.detectors import VALUE_DETECTORS, name_hint

MAX_SAMPLE_ROWS = 500
DETECTOR_SHARE = 0.3  # share of non-null sampled values that must match
MIN_DETECTOR_MATCHES = 2
_LABEL_ORDER = ("public", "internal", "confidential", "restricted")


@dataclass(frozen=True)
class ColumnFinding:
    name: str
    sql_type: str
    label: str
    reasons: tuple[str, ...]
    sampled: int  # non-null values looked at
    distinct: int
    enum_values: tuple[str, ...] | None = None


@dataclass(frozen=True)
class TableFinding:
    name: str
    label: str
    columns: tuple[ColumnFinding, ...]
    sampled_rows: int
    source: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return None  # binary is never inspected, never enumerated
    text = str(value).strip()
    return text or None


def classify_column(name: str, sql_type: str, values: list[object]) -> ColumnFinding:
    texts = [t for t in (_as_text(v) for v in values) if t is not None]
    counts = Counter(texts)
    reasons: list[str] = []
    label = None

    if texts:
        threshold = max(MIN_DETECTOR_MATCHES, int(len(texts) * DETECTOR_SHARE + 0.999))
        for detector_name, detector in VALUE_DETECTORS.items():
            matches = sum(1 for t in texts if detector(t))
            if matches >= threshold or (matches and matches == len(texts)):
                label = "restricted"
                reasons.append(f"values:{detector_name}:{matches}/{len(texts)}")
    hinted, token = name_hint(name)
    if hinted is not None:
        reasons.append(f"name:{token}")
        if label is None or _LABEL_ORDER.index(hinted) > _LABEL_ORDER.index(label):
            label = hinted
    if label is None:
        label = "internal"
        reasons.append("default:operational")

    enum_values = None
    if label == "internal" and texts and len(counts) <= ENUM_MAX_VALUES:
        repeated = all(n >= 2 for n in counts.values())
        short = all(len(v) <= ENUM_MAX_VALUE_LENGTH for v in counts)
        if repeated and short:
            enum_values = tuple(sorted(counts))
            reasons.append(f"enum:{len(counts)}")
    return ColumnFinding(
        name=name,
        sql_type=sql_type,
        label=label,
        reasons=tuple(reasons),
        sampled=len(texts),
        distinct=len(counts),
        enum_values=enum_values,
    )


def classify_table(
    name: str, columns: list[tuple[str, str]], rows: list[tuple], source: str = ""
) -> TableFinding:
    """Classify every column of one sampled table. `rows` are at most 500 tuples."""
    rows = rows[:MAX_SAMPLE_ROWS]
    findings = tuple(
        classify_column(col_name, sql_type, [row[i] if i < len(row) else None for row in rows])
        for i, (col_name, sql_type) in enumerate(columns)
    )
    # The table label is the floor every column shares. Columns carry their
    # own (possibly higher) labels; column-level policy does the rest.
    notes = ()
    if not rows:
        notes = ("empty sample: classification from column names only",)
    return TableFinding(
        name=name,
        label="internal",
        columns=findings,
        sampled_rows=len(rows),
        source=source,
        notes=notes,
    )
