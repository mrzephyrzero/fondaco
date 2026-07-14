# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Blind planner client: annotated schema + question in, plan out.

The request surface is structurally incapable of carrying row data — it
serializes only `AnnotatedSchema` (labels, types, statistics, comments;
never rows, by contract §2.1) plus the user question, and on repair
rounds only machine-readable validation/policy errors. The transport is
injectable so tests can capture every outbound byte (canary test).

Base URL is configurable: any OpenAI-compatible /chat/completions
endpoint (cloud API, LiteLLM/Bifrost gateway, or local Ollama) works
with zero code changes.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field

import httpx

from boundary.validator import ValidationResult, validate_plan
from executor.adapters.contract import AnnotatedSchema

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
DEFAULT_PROMPT_VERSION = "v3"


class PlannerError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class PlanAttempt:
    plan: dict | None
    validation: ValidationResult | None
    parse_error: str | None = None


@dataclass(frozen=True)
class PlanningTrace:
    """Full attempt history, for the audit log (wired in Phase 4)."""

    question: str
    prompt_version: str
    attempts: tuple[PlanAttempt, ...] = field(default_factory=tuple)


def _load_prompt(version: str) -> str:
    path = os.path.join(_PROMPTS_DIR, f"{version}.md")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _schema_payload(schema: AnnotatedSchema) -> str:
    """Compact JSON view of the schema: metadata and statistics only."""
    return json.dumps(
        {
            "tables": [
                {
                    "name": t.name,
                    "label": t.label,
                    "row_count": t.row_count,
                    "comment": t.comment,
                    "columns": [
                        {"name": c.name, "type": c.sql_type, "label": c.label, "comment": c.comment}
                        for c in t.columns
                    ],
                }
                for t in schema.tables
            ]
        },
        separators=(",", ":"),
    )


def _extract_steps(raw_text: str) -> list:
    """Strictly parse the model output: one JSON object with a 'steps' list."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    parsed = json.loads(text)  # ValueError → caller counts a failed attempt
    if not isinstance(parsed, dict) or not isinstance(parsed.get("steps"), list):
        raise ValueError("model output is not an object with a 'steps' list")
    return parsed["steps"]


class PlannerClient:
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        model: str = "",
        max_attempts: int = 2,
        timeout_s: float = 60.0,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        sampling: dict[str, object] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_attempts = max(1, max_attempts)
        self._timeout_s = timeout_s
        self._prompt_version = prompt_version
        self._system_prompt = _load_prompt(prompt_version)
        # Sampling params are endpoint-profile configuration, not client logic:
        # a profile whose model rejects a param (e.g. temperature) simply does
        # not set it. The client never inspects the model name.
        self._sampling = {"temperature": 0} if sampling is None else dict(sampling)
        self._transport = transport

    def _complete(self, messages: list[dict]) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body = {"model": self._model, "messages": messages, **self._sampling}
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_s) as client:
                response = client.post(
                    f"{self._base_url}/chat/completions", headers=headers, json=body
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            raise PlannerError("llm_unreachable", type(exc).__name__) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PlannerError("llm_bad_response", type(exc).__name__) from exc

    def generate_plan(self, question: str, schema: AnnotatedSchema) -> tuple[dict, PlanningTrace]:
        """Return a *validated* plan or raise PlannerError (fail closed).

        Repair rounds receive only machine-readable errors — never data.
        """
        messages = [
            {"role": "system", "content": self._system_prompt},
            {
                "role": "user",
                "content": f"SCHEMA:\n{_schema_payload(schema)}\n\nQUESTION:\n{question}",
            },
        ]
        attempts: list[PlanAttempt] = []
        for _ in range(self._max_attempts):
            raw = self._complete(messages)
            try:
                steps = _extract_steps(raw)
            except (ValueError, TypeError) as exc:
                attempts.append(
                    PlanAttempt(plan=None, validation=None, parse_error=type(exc).__name__)
                )
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": "Output was not a single JSON object with a 'steps' list. "
                        "Reply with exactly that and nothing else.",
                    }
                )
                continue

            plan = {
                "dsl_version": "v0",
                "plan_id": str(uuid.uuid4()),  # boundary-assigned, never the model's
                "question": question,
                "steps": steps,
            }
            validation = validate_plan(plan)
            attempts.append(PlanAttempt(plan=plan, validation=validation))
            if validation.valid:
                trace = PlanningTrace(
                    question=question,
                    prompt_version=self._prompt_version,
                    attempts=tuple(attempts),
                )
                return plan, trace

            errors_payload = json.dumps(
                [{"code": e.code, "path": e.path, "detail": e.detail} for e in validation.errors]
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "The plan failed validation. Fix these errors and reply with the "
                    f"corrected JSON object only:\n{errors_payload}",
                }
            )

        raise PlannerError(
            "attempts_exhausted",
            f"no valid plan within {self._max_attempts} attempts",
        )


def sampling_from_env() -> dict[str, object]:
    """Sampling params for the configured endpoint profile.

    `FONDACO_LLM_TEMPERATURE` unset (or "omit") sends no temperature at all —
    the cloud default profile, whose model rejects the deprecated param. A
    profile that wants it (e.g. local Ollama, for reproducibility) sets a
    number explicitly.
    """
    raw = os.environ.get("FONDACO_LLM_TEMPERATURE")
    if raw is None or raw.strip().lower() in ("", "omit", "none"):
        return {}
    try:
        return {"temperature": float(raw)}
    except ValueError:  # fail safe: send nothing rather than a bogus param
        return {}


def client_from_env(transport: httpx.BaseTransport | None = None) -> PlannerClient:
    return PlannerClient(
        base_url=os.environ.get("FONDACO_LLM_BASE_URL", "https://api.anthropic.com/v1"),
        api_key=os.environ.get("FONDACO_LLM_API_KEY", ""),
        model=os.environ.get("FONDACO_LLM_MODEL", "claude-sonnet-5"),
        max_attempts=int(os.environ.get("FONDACO_LLM_MAX_ATTEMPTS", "2")),
        timeout_s=float(os.environ.get("FONDACO_LLM_TIMEOUT_S", "60")),
        sampling=sampling_from_env(),
        transport=transport,
    )
