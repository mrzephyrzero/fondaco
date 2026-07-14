# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""Reuse the integration DB fixtures for the adversarial suite."""

from tests.integration.conftest import (  # noqa: F401 — re-exported as fixtures
    adapter,
    requires_db,
    seeded_database,
)
