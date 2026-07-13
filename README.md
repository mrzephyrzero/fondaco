# Fondaco

> **Data stays home. Plans cross.**

A data boundary for AI over enterprise data. The LLM acts as a *blind planner*: it sees only your annotated schema and a question — never row data — and produces an inspectable JSON plan. The plan is validated, checked against a classification-label policy, approved by a human, and executed deterministically inside your perimeter against a read-only database role. Every crossing is written to an append-only, hash-chained audit log.

**Status:** Phase 0 (foundations). Not usable yet. See `STATE.md` for progress and `IMPLEMENTATION_PLAN.md` for the full plan.

## Quick start (skeleton)

```sh
docker compose up
curl http://localhost:8000/health
```

Architecture overview, demo walkthrough, and the "What this does NOT protect against" section land in Phases 4–7.
