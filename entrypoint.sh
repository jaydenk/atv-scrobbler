#!/bin/sh
set -e

# Ensure data directory exists
mkdir -p /app/data

# If arguments are passed, run them (e.g. atvremote scan, atvremote wizard)
if [ $# -gt 0 ]; then
    exec "$@"
fi

# Default: run the scrobbler
exec python -m atv_scrobbler
