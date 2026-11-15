# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Deterministic detectors for auto-discovery. Stdlib only, no network, no LLM.

Value detectors validate check digits where the format has one (Italian
codice fiscale, partita IVA, IBAN mod-97), so a column of random 16-char
strings does not pass as fiscal codes. Name hints match whole tokens of a
column name (`cf`, `cliente_cf`, `iban_fornitore`), never substrings, so
`price_cfg` does not read as a fiscal code.
"""

from __future__ import annotations

import ipaddress
import re
import string

# ── Name hints ────────────────────────────────────────────────────────────

RESTRICTED_NAME_TOKENS = frozenset(
    {
        # payroll
        "stipendio", "stipendi", "ral", "salary", "salario", "wage", "wages", "payroll",
        "retribuzione", "compenso",
        # credentials / secrets
        "password", "passwd", "pwd", "token", "secret", "apikey", "api_key", "hash", "pin",
        # identifiers
        "cf", "codfisc", "codice_fiscale", "codicefiscale", "fiscal_code", "ssn", "tax_id",
        "taxid", "passport", "passaporto", "iban", "bic", "swift", "piva", "partita_iva",
        "vat", "vat_number", "carta", "card", "pan", "cvv",
        # contact / identity PII
        "email", "mail", "pec", "phone", "telefono", "cellulare", "mobile", "tel",
        "address", "indirizzo", "via", "cap", "zip", "nome", "cognome", "name", "surname",
        "first_name", "last_name", "firstname", "lastname", "birth", "nascita",
        "birthdate", "data_nascita", "dob", "ip", "ip_address", "health", "salute",
        "diagnosi", "diagnosis",
    }
)  # fmt: skip

CONFIDENTIAL_NAME_TOKENS = frozenset(
    {
        "margin", "margine", "marginalita", "cost", "costo", "costi", "price", "prezzo",
        "prezzi", "unit_price", "prezzo_unitario", "discount", "sconto", "sconti",
        "revenue", "fatturato", "ricavo", "ricavi", "profit", "utile", "markup",
        "listino", "budget", "forecast", "commissione", "provvigione",
    }
)  # fmt: skip


def name_tokens(name: str) -> set[str]:
    """Whole tokens of a column name, plus adjacent pairs (`unit_price`)."""
    lowered = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).lower()
    parts = [p for p in re.split(r"[^a-z0-9]+", lowered) if p]
    tokens = set(parts)
    tokens.update(f"{a}_{b}" for a, b in zip(parts, parts[1:], strict=False))
    tokens.add("_".join(parts))
    return tokens


def name_hint(name: str) -> tuple[str | None, str | None]:
    """(label, matched token) from the column name, restricted first."""
    tokens = name_tokens(name)
    for token in sorted(tokens & RESTRICTED_NAME_TOKENS):
        return "restricted", token
    for token in sorted(tokens & CONFIDENTIAL_NAME_TOKENS):
        return "confidential", token
    return None, None


# ── Value detectors ───────────────────────────────────────────────────────

_CF_RE = re.compile(
    r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-EHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$"
)
_CF_ODD = dict(
    zip(
        string.digits + string.ascii_uppercase,
        [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20,
         11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23],
        strict=True,
    )
)  # fmt: skip


def is_codice_fiscale(value: str) -> bool:
    """Italian personal fiscal code, including omocodia substitutions, with check char."""
    v = value.strip().upper()
    if not _CF_RE.match(v):
        return False
    total = 0
    for i, ch in enumerate(v[:15]):
        if i % 2 == 0:  # odd positions (1-based)
            total += _CF_ODD[ch]
        else:
            total += int(ch) if ch.isdigit() else ord(ch) - ord("A")
    return v[15] == chr(ord("A") + total % 26)


def is_partita_iva(value: str) -> bool:
    """Italian VAT number: 11 digits, Luhn-style check digit."""
    v = value.strip().upper().removeprefix("IT")
    if not re.fullmatch(r"[0-9]{11}", v) or v == "0" * 11:
        return False
    total = 0
    for i, ch in enumerate(v[:10]):
        digit = int(ch)
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10 == int(v[10])


def is_iban(value: str) -> bool:
    """Any-country IBAN validated by ISO 13616 mod-97."""
    v = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", v):
        return False
    rearranged = v[4:] + v[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def is_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


_PHONE_RE = re.compile(r"^(?:\+|00)?[0-9][0-9 ()./-]{6,18}[0-9]$")
_NOT_PHONE_RE = re.compile(
    r"^(?:[0-9]{4}[-/.][0-9]{1,2}[-/.][0-9]{1,2}"  # 2024-01-15
    r"|[0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{2,4}"  # 15/01/2024
    r"|-?[0-9]+[.,][0-9]+)$"  # 1234567.89
)


def is_phone(value: str) -> bool:
    """International or Italian phone number: 8–15 digits with usual separators."""
    v = value.strip()
    if not _PHONE_RE.match(v):
        return False
    if _NOT_PHONE_RE.match(v):
        return False  # dates and decimal amounts share the alphabet of phones
    digits = re.sub(r"[^0-9]", "", v)
    if not 8 <= len(digits) <= 15:
        return False
    if v.isdigit():
        # A bare run of digits is far more often a code or an amount than a
        # phone: only the Italian mobile (3xx, 10 digits) and landline (0x,
        # 9-11 digits) shapes count.
        return (v[0] == "3" and len(v) == 10) or (v[0] == "0" and 9 <= len(v) <= 11)
    return True


def is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return True


VALUE_DETECTORS: dict[str, object] = {
    "codice_fiscale": is_codice_fiscale,
    "partita_iva": is_partita_iva,
    "iban": is_iban,
    "email": is_email,
    "phone": is_phone,
    "ip_address": is_ip_address,
}
