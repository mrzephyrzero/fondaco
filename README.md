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

## Guards (aggregate-exfiltration mitigation)

Even when every individual plan is valid and policy-approved, a *sequence*
of aggregate questions can binary-search a single row's value. Two guards
(`boundary/guards.py`) raise the cost and make it visible — they do not
close the channel:

- **k-threshold** (`FONDACO_GUARD_K`, default 5): any aggregate group
  computed from fewer than k input rows is dropped entirely (not masked).
  This removes the one-row `count` that a binary search depends on.
- **Per-session query budget** (`FONDACO_QUERY_BUDGET`, default 20): a hard
  stop on executed query steps; over budget, the plan is refused, unrun,
  and recorded.

Both fail closed: a config value below 1 or unparsable reverts to the
default — a guard cannot be disabled.

## What this does NOT protect against

_Draft, finalized in Phase 7's threat model. Fondaco is a reference
architecture; this list is deliberately honest._

- **Aggregate/inference channel — raised in cost, not closed.** The
  k-threshold and query budget slow statistical inference and make it
  auditable, but a patient attacker within budget, or across sessions, can
  still learn distributional facts. Fondaco protects against *row data
  crossing the boundary*, not against all inference over aggregates.
- **No authentication in V1.** The approver identity is self-declared and
  the query budget is keyed on a session cookie, so clearing cookies grants
  a fresh budget. The mitigation is the append-only audit trail, not
  prevention. Per-user identity/authz is a deployment concern (roadmap).
- **Differencing across groupings.** The k-threshold suppresses small
  groups within one result but does not defend against combining two
  different legal aggregates to isolate an individual.
- **A malicious approver.** Approval is the trust anchor; someone who
  approves an exfiltrating plan is out of scope by design.
- **Side channels** (timing, error text, query duration) beyond those
  already sanitized are examined in Phase 7.

Architecture overview and demo walkthrough land in Phase 6.
