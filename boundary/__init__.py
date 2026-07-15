# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Security-critical core of Fondaco.

Every file in this package is boundary code (IMPLEMENTATION_PLAN.md §0.5):
parametrized queries only, fail closed on every error path, no dynamic
eval, all LLM-derived input treated as hostile. Subject to adversarial
review in Phase 7.
"""
