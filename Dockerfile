# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY api ./api
COPY boundary ./boundary
COPY planner ./planner
COPY executor ./executor

# The image also carries the MCP server (`fondaco mcp`), run as its own
# process, and DuckDB for the zero-config demo (docker-compose.demo.yml).
RUN pip install --no-cache-dir ".[mcp,duckdb]"

# Run unprivileged: the app needs to write only its data directory (audit log,
# demo dataset, demo keys). /data is created owned by that user, so a fresh
# named volume mounted there inherits the ownership.
RUN useradd --system --uid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin fondaco \
    && mkdir -p /data \
    && chown fondaco:fondaco /data
USER fondaco

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
