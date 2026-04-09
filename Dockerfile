FROM python:3.12-slim AS build

WORKDIR /app

# Build deps for cffi/chacha20poly1305 (needed by pyatv on arm64)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

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

ENTRYPOINT ["/app/entrypoint.sh"]
