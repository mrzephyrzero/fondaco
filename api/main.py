# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""FastAPI application entry point.

Phase 0 exposes only GET /health. The ask/approve/audit endpoints are
built in Phase 4.
"""

import os

import psycopg
from fastapi import FastAPI

app = FastAPI(title="Fondaco", description="Data stays home. Plans cross.")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness plus a best-effort database reachability check."""
    dsn = os.environ.get("DATABASE_URL", "")
    db_status = "unconfigured"
    if dsn:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            db_status = "ok"
        except psycopg.Error:
            db_status = "unreachable"
    return {"status": "ok", "db": db_status}
