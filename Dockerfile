FROM python:3.12-slim AS build

WORKDIR /app

COPY pyproject.toml .
COPY atv_scrobbler/ atv_scrobbler/
RUN pip install --no-cache-dir .

FROM python:3.12-slim

WORKDIR /app

# Create non-root user
RUN groupadd -r scrobbler && useradd -r -g scrobbler scrobbler

# Copy installed packages from build stage
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

# Copy application code
COPY atv_scrobbler/ atv_scrobbler/

# Runtime files (tokens, logs) are written to /app — must be writable
RUN chown -R scrobbler:scrobbler /app

USER scrobbler

# Healthcheck: verify the Python process is running
# Will be upgraded to heartbeat file check in Task 2
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

ENTRYPOINT ["python", "-m", "atv_scrobbler"]
