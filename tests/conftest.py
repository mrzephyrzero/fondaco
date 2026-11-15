# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Suite-wide setup.

The audit log refuses to run without an HMAC key (boundary/audit.py), and
`api.main` builds an app at import time. Set a test-only key before any test
module is imported, so the suite exercises the real keyed path everywhere.
"""

import os

TEST_AUDIT_KEY = "test-only-audit-hmac-key-0123456789abcdef"

os.environ.setdefault("FONDACO_AUDIT_HMAC_KEY", TEST_AUDIT_KEY)

# The approval UI fails closed: with no approvers configured it would mint a
# random bootstrap token per app. Configure fixed test approvers instead
# (tests/ui_auth.py), so tests sign in exactly as a deployment would.
from tests.ui_auth import approvers_env  # noqa: E402

os.environ.setdefault("FONDACO_APPROVERS", approvers_env())
