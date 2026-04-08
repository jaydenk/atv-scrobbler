FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY atv_scrobbler/ atv_scrobbler/

ENTRYPOINT ["python", "-m", "atv_scrobbler"]
