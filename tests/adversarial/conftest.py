# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Reuse the integration DB fixtures for the adversarial suite."""

from tests.integration.conftest import (  # noqa: F401 — re-exported as fixtures
    adapter,
    requires_db,
    seeded_database,
)
