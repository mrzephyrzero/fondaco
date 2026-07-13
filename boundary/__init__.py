# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Security-critical core of Fondaco.

Every file in this package is boundary code (IMPLEMENTATION_PLAN.md §0.5):
parametrized queries only, fail closed on every error path, no dynamic
eval, all LLM-derived input treated as hostile. Subject to adversarial
review in Phase 7.
"""
