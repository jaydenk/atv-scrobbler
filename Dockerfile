FROM python:3.12-slim AS build

WORKDIR /app

COPY pyproject.toml .
COPY atv_scrobbler/ atv_scrobbler/
RUN pip install --no-cache-dir .

FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from build stage
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

# Copy application code and entrypoint
COPY atv_scrobbler/ atv_scrobbler/
COPY entrypoint.sh /app/entrypoint.sh

# Healthcheck: verify the asyncio event loop is alive by checking heartbeat freshness
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import time; from pathlib import Path; t=float(Path('/app/heartbeat').read_text()); assert time.time()-t < 60" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
