# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY api ./api
COPY boundary ./boundary
COPY planner ./planner
COPY executor ./executor

RUN pip install --no-cache-dir .

RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
