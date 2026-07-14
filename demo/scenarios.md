# Demo scenarios

The 10 scripted demo questions (Checkpoint P3: ≥ 8 must yield a valid,
policy-passing plan within 2 attempts at clearance `internal`).

These same questions are the **demo-mode fixtures**: `planner/demo.py` holds a
hand-written, validated plan for each, so `docker compose up` gives a keyless
demo (no LLM) while every plan still crosses the real boundary. Editing a
question here means updating its fixture there (the integration test
`tests/integration/test_demo_scenarios.py` enforces they stay in sync).

Questions are phrased to be answerable with the v0 DSL over the demo
dataset while keeping raw query results under the adapter's 10 000-row cap
(filter or project narrowly, aggregate in plan steps).

| # | Question | Expected outcome |
|---|---|---|
| 1 | How many orders were placed per region since October 2025? | pass (`internal`) |
| 2 | What is the total revenue per region for orders placed in 2026? | pass (`internal`) |
| 3 | What was the average order value per order status in 2026? | pass (`internal`) |
| 4 | How many deliveries did each carrier handle in 2026? | pass (`internal`) |
| 5 | How many products do we have per category? | pass (`public`) |
| 6 | What is the highest single order amount recorded in 2026? | pass (`internal`) |
| 7 | What total quantity moved out of each warehouse in 2026 (outbound movements only)? | pass (`internal`) |
| 8 | How many orders were cancelled per region in 2026? | pass (`internal`) |
| 9 | How many orders were placed per month in the first half of 2026? | pass (`internal`) |
| 10 | List the names and emails of customers in Venezia. | **policy DENY** — touches `customers` (restricted PII) above `internal` clearance; demonstrates the deny path |

Scenario 10 is *supposed* to be refused: a correct plan for it can never
pass policy at `internal` clearance, so the checkpoint target is 8 of the
9 answerable questions.
