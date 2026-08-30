#!/bin/sh
set -eu

mkdir -p /app/data

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-99}"
  PGID="${PGID:-100}"
  current="$(stat -c %u /app/data 2>/dev/null || echo 0)"
  if [ "$current" != "$PUID" ]; then
    chown -R "${PUID}:${PGID}" /app/data || true
  fi
  exec gosu "${PUID}:${PGID}" "$0" "$@"
fi

export ENVIRONMENT="${ENVIRONMENT:-production}"
export TZ="${TZ:-Europe/Copenhagen}"
export JOB_WORKER_EMBEDDED="${JOB_WORKER_EMBEDDED:-1}"

if [ "$ENVIRONMENT" = "production" ] && [ -z "${JWT_SECRET:-}" ]; then
  echo "JWT_SECRET is required when ENVIRONMENT=production" >&2
  exit 1
fi

WEB_WORKERS="${WEB_WORKERS:-1}"
JOB_WORKERS="${JOB_WORKERS:-0}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"

start_job_workers() {
  i=0
  while [ "$i" -lt "$JOB_WORKERS" ]; do
    JOB_WORKER_EMBEDDED=0 python -m worker &
    i=$((i + 1))
  done
}

if [ "$JOB_WORKERS" -le 0 ]; then
  exec uvicorn main:app --host 0.0.0.0 --port 8502 --workers "$WEB_WORKERS" \
    --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS"
fi

uvicorn main:app --host 0.0.0.0 --port 8502 --workers "$WEB_WORKERS" \
  --proxy-headers --forwarded-allow-ips "$FORWARDED_ALLOW_IPS" &
UVICORN_PID=$!
sleep 2
start_job_workers

term() {
  kill -TERM "$UVICORN_PID" 2>/dev/null || true
}

trap term TERM INT
wait "$UVICORN_PID"
status=$?
term
wait || true
exit "$status"
