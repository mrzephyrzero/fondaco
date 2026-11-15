# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""Local auto-discovery & classification (`fondaco discover`).

Runs only on the operator's machine: no LLM, no network beyond the database
being scanned. Samples at most 500 rows per table in memory, never persists
them, and writes a reviewable annotation proposal (JSON/YAML, optionally
`COMMENT ON` SQL for Postgres) for the Data Owner to confirm.
"""
