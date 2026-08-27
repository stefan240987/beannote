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
export JOB_WORKER_EMBEDDED="${JOB_WORKER_EMBEDDED:-1}"

WEB_WORKERS="${WEB_WORKERS:-2}"
JOB_WORKERS="${JOB_WORKERS:-0}"

start_job_workers() {
  i=0
  while [ "$i" -lt "$JOB_WORKERS" ]; do
    JOB_WORKER_EMBEDDED=0 python -m worker &
    i=$((i + 1))
  done
}

if [ "$JOB_WORKERS" -le 0 ]; then
  exec uvicorn main:app --host 0.0.0.0 --port 8501 --workers "$WEB_WORKERS"
fi

uvicorn main:app --host 0.0.0.0 --port 8501 --workers "$WEB_WORKERS" &
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
