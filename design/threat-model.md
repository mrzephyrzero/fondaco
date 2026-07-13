# Threat Model

**Status:** SKELETON — written in Phase 7 (adversarial phase). Do not fill in earlier phases.

This document is a deliverable, not an internal note: it ships with the project. Every attack attempted in Phase 7 is logged here with its outcome — fixed, mitigated, or accepted residual risk.

## 1. Security claim under test

Row data from connected sources never crosses the boundary to the LLM planner or any unapproved egress. (See README architecture section.)

## 2. Assets & trust boundaries

_TODO (Phase 7): diagram + list — data source, boundary, planner, approval UI, audit log._

## 3. Attacker profiles

_TODO (Phase 7): e.g., malicious question author; compromised/hostile LLM; hostile schema annotations (column comments); curious approved user driving inference channels._

## 4. Attack log

| # | Attack | Vector | Result | Mitigation / accepted risk |
|---|---|---|---|---|
| — | _≥ 10 entries required by Checkpoint P7_ | | | |

Candidate attacks (from IMPLEMENTATION_PLAN.md §Phase 7): plan-DSL smuggling, SQL template escapes, label escalation via derived results, exfiltration via error messages, prompt injection from schema annotations, canary extraction via repair loop, binary-search aggregate exfiltration.

## 5. Residual risks

_TODO (Phase 7): honest list; feeds README "What this does NOT protect against"._
