# Fondaco

> **Data stays home. Plans cross.**

A data boundary for AI over enterprise data. The LLM acts as a *blind planner*: it sees only your annotated schema and a question — never row data — and produces an inspectable JSON plan. The plan is validated, checked against a classification-label policy, approved by a human, and executed deterministically inside your perimeter against a read-only database role. Every crossing is written to an append-only, hash-chained audit log.

**Status:** Phase 0 (foundations). Not usable yet. See `STATE.md` for progress and `IMPLEMENTATION_PLAN.md` for the full plan.

## Quick start (skeleton)

```sh
docker compose up
curl http://localhost:8000/health
```

## Planner configuration

The planner talks to any OpenAI-compatible `/chat/completions` endpoint;
the base URL is configuration, not code. Set in `.env`:

```sh
FONDACO_LLM_BASE_URL=https://api.anthropic.com/v1   # cloud API…
FONDACO_LLM_API_KEY=sk-...
FONDACO_LLM_MODEL=claude-sonnet-5
```

**Ollama smoke test** (zero code changes): install Ollama, `ollama pull llama3.1`, then

```sh
FONDACO_LLM_BASE_URL=http://localhost:11434/v1
FONDACO_LLM_MODEL=llama3.1
```

and re-run the scenario suite: `pytest tests/integration/test_scenarios_llm.py`.
The same swap works for LiteLLM/Bifrost gateways.

Architecture overview, demo walkthrough, and the "What this does NOT protect against" section land in Phases 4–7.
