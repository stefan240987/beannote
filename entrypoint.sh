#!/bin/sh
set -eu

mkdir -p /app/data

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-99}"
  PGID="${PGID:-100}"
  chown -R "${PUID}:${PGID}" /app/data || true
fi

export ENVIRONMENT="${ENVIRONMENT:-production}"
export TZ="${TZ:-Europe/Copenhagen}"

exec streamlit run app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.headless=true
