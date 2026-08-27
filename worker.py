"""Background OCR/enrich worker. Safe to run as a process or an embedded thread."""

from __future__ import annotations

import os
import threading
import time
import traceback

from db import connect
from jobs import ensure_schema_on, poll_interval, sync_gemini_slots_on, worker_tick

_started = False
_start_lock = threading.Lock()


def _truthy(name: str, default: str = "1") -> bool:
    return (os.getenv(name) or default).strip().lower() not in {"0", "false", "no", "off"}


def _worker_id() -> str:
    ident = threading.get_ident()
    return f"{os.getpid()}-{ident}-{os.urandom(2).hex()}"


def run_forever() -> None:
    try:
        with connect() as conn:
            ensure_schema_on(conn)
            sync_gemini_slots_on(conn)
    except Exception as exc:
        print(f"job worker schema: {exc}")
    worker_id = _worker_id()
    print(f"job worker {worker_id} started")
    while True:
        try:
            ran = worker_tick(worker_id)
            if not ran:
                time.sleep(poll_interval())
        except Exception as exc:
            print(f"job worker loop: {exc}")
            traceback.print_exc()
            time.sleep(1.0)


def start_embedded_worker() -> None:
    """One daemon thread per process unless JOB_WORKER_EMBEDDED=0."""
    global _started
    if not _truthy("JOB_WORKER_EMBEDDED", "1"):
        return
    with _start_lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=run_forever, name="beannote-jobs", daemon=True)
    thread.start()


if __name__ == "__main__":
    os.environ.setdefault("JOB_WORKER_EMBEDDED", "0")
    run_forever()
