# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY api ./api
COPY boundary ./boundary
COPY planner ./planner
COPY executor ./executor

# The image also carries the MCP server (`fondaco mcp`), run as its own
# process, and DuckDB for the zero-config demo (docker-compose.demo.yml).
RUN pip install --no-cache-dir ".[mcp,duckdb]"

RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
