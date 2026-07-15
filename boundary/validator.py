# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Plan validation against the Plan DSL v0 (design/plan-dsl.md §4).

Two layers, both mandatory: JSON Schema (boundary/plan_schema.json) and
structural rules the schema cannot express. Plans are LLM output and
treated as hostile; any internal fault invalidates the plan (fail
closed). This module never repairs input and never raises to callers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import jsonschema

_SCHEMA = json.loads((Path(__file__).with_name("plan_schema.json")).read_text(encoding="utf-8"))
_SCHEMA_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA)

_PLACEHOLDER_RE = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|GRANT|COPY|CALL|DO|INTO)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ValidationError:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[ValidationError, ...] = ()


def _err(code: str, path: str, detail: str) -> ValidationError:
    return ValidationError(code=code, path=path, detail=detail)


def _param_value_matches(dsl_type: str, value: object) -> bool:
    # bool is an int subclass in Python; exclude it from numeric types.
    if dsl_type == "string":
        return isinstance(value, str)
    if dsl_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if dsl_type == "float":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if dsl_type == "bool":
        return isinstance(value, bool)
    if dsl_type == "date":
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if dsl_type == "timestamp":
        if not isinstance(value, str):
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    return False


def _check_template(step_path: str, step: dict, errors: list[ValidationError]) -> None:
    template: str = step["template"]
    if not _SELECT_RE.match(template):
        errors.append(_err("template_not_select", step_path, "template must begin with SELECT"))
    if ";" in template:
        errors.append(_err("multi_statement", step_path, "';' is forbidden in templates"))
    if "--" in template or "/*" in template:
        errors.append(_err("comment_forbidden", step_path, "SQL comments are forbidden"))
    if "{" in template or "}" in template:
        errors.append(_err("interpolation_forbidden", step_path, "braces are forbidden"))
    match = _FORBIDDEN_KEYWORD_RE.search(template)
    if match:
        errors.append(
            _err("forbidden_keyword", step_path, f"forbidden keyword: {match.group(1).upper()}")
        )

    placeholders = set(_PLACEHOLDER_RE.findall(template))
    if _PLACEHOLDER_RE.sub("", template).count("%"):
        errors.append(
            _err("invalid_placeholder", step_path, "'%' outside %(name)s placeholder form")
        )
    declared = set(step["params"].keys())
    for name in sorted(placeholders - declared):
        errors.append(_err("undeclared_param", step_path, f"placeholder not declared: {name}"))
    for name in sorted(declared - placeholders):
        errors.append(_err("unused_param", step_path, f"declared param not in template: {name}"))
    for name, spec in step["params"].items():
        if not _param_value_matches(spec["type"], spec["value"]):
            errors.append(
                _err(
                    "param_type_mismatch",
                    f"{step_path}.params.{name}",
                    f"value does not match declared type {spec['type']!r}",
                )
            )


def _structural_errors(plan: dict) -> list[ValidationError]:
    errors: list[ValidationError] = []
    steps: list[dict] = plan["steps"]

    expected_ids = [f"s{i + 1}" for i in range(len(steps))]
    actual_ids = [step["id"] for step in steps]
    if actual_ids != expected_ids:
        errors.append(
            _err("step_id_sequence", "steps", f"ids must be {expected_ids}, got {actual_ids}")
        )

    present_positions = [i for i, step in enumerate(steps) if step["type"] == "present"]
    if len(present_positions) != 1 or present_positions[0] != len(steps) - 1:
        errors.append(
            _err("present_misplaced", "steps", "exactly one present step, in final position")
        )

    seen: set[str] = set()
    for i, step in enumerate(steps):
        step_path = f"steps[{i}]"
        if step["type"] in ("aggregate", "present"):
            input_id = step["input"]
            if input_id not in seen:
                errors.append(
                    _err("bad_reference", step_path, f"input {input_id!r} is not an earlier step")
                )
        if step["type"] == "aggregate":
            for j, op in enumerate(step["ops"]):
                if op["column"] == "*" and op["op"] != "count":
                    errors.append(
                        _err(
                            "invalid_aggregate_column",
                            f"{step_path}.ops[{j}]",
                            "'*' is only valid for count",
                        )
                    )
        if step["type"] == "query":
            _check_template(step_path, step, errors)
        seen.add(step["id"])
    return errors


def validate_plan(raw: object) -> ValidationResult:
    """Validate an untrusted plan. Never raises; any fault → invalid."""
    try:
        schema_errors = [
            _err("schema_violation", "/".join(str(p) for p in e.absolute_path), e.message)
            for e in _SCHEMA_VALIDATOR.iter_errors(raw)
        ]
        if schema_errors:
            return ValidationResult(valid=False, errors=tuple(schema_errors))
        assert isinstance(raw, dict)  # noqa: S101 — guaranteed by schema; for the type checker
        errors = _structural_errors(raw)
        return ValidationResult(valid=not errors, errors=tuple(errors))
    except Exception as exc:  # fail closed: an internal fault is a validation failure
        detail = f"internal validation fault: {type(exc).__name__}"
        return ValidationResult(valid=False, errors=(_err("validator_error", "", detail),))
