#!/bin/sh
set -eu

mkdir -p /app/data /app/static

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-99}"
  PGID="${PGID:-100}"
  chown -R "${PUID}:${PGID}" /app/data || true
fi

export ENVIRONMENT="${ENVIRONMENT:-production}"
export TZ="${TZ:-Europe/Copenhagen}"

exec uvicorn main:app --host 0.0.0.0 --port 8501
