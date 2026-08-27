"""SQLite job queue, Gemini slots, and shared product-lookup cache.

Scan/enrich run in worker threads or a dedicated process so the FastAPI
event loop stays free for other users.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from db import _now, connect, get_db_path

JOB_KINDS = ("scan", "enrich")


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.05, float(raw))
    except ValueError:
        return default


def ocr_max_concurrent() -> int:
    return max(1, _env_int("OCR_MAX_CONCURRENT", 2))


def scan_rate_per_minute() -> int:
    return max(1, _env_int("SCAN_RATE_PER_MINUTE", 8))


def enrich_rate_per_minute() -> int:
    return max(1, _env_int("ENRICH_RATE_PER_MINUTE", 4))


def max_queued_jobs() -> int:
    return max(1, _env_int("MAX_QUEUED_JOBS", 80))


def job_timeout_sec() -> int:
    return max(30, _env_int("JOB_TIMEOUT_SEC", 240))


def poll_interval() -> float:
    return _env_float("JOB_POLL_INTERVAL", 0.4)


def get_jobs_dir() -> Path:
    path = get_db_path().parent / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _immediate() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_db_path()), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_schema_on(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            user_id INTEGER,
            payload TEXT NOT NULL DEFAULT '{}',
            result TEXT,
            error TEXT DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);
        CREATE INDEX IF NOT EXISTS idx_jobs_user_kind_created ON jobs(user_id, kind, created_at);

        CREATE TABLE IF NOT EXISTS gemini_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER,
            taken_at TEXT
        );

        CREATE TABLE IF NOT EXISTS product_lookup_cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS worker_heartbeats (
            worker_id TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        );
        """
    )


def sync_gemini_slots_on(conn: sqlite3.Connection) -> None:
    wanted = ocr_max_concurrent()
    rows = conn.execute("SELECT id, job_id FROM gemini_slots ORDER BY id").fetchall()
    if len(rows) < wanted:
        for _ in range(wanted - len(rows)):
            conn.execute("INSERT INTO gemini_slots (job_id, taken_at) VALUES (NULL, NULL)")
        return
    extras = [row["id"] for row in rows if row["job_id"] is None]
    overflow = len(rows) - wanted
    for slot_id in extras[:overflow]:
        conn.execute("DELETE FROM gemini_slots WHERE id = ? AND job_id IS NULL", (slot_id,))


def _job_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = row["payload"]
    try:
        parsed_payload = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        parsed_payload = {}
    result = row["result"]
    try:
        parsed_result = json.loads(result) if result else None
    except json.JSONDecodeError:
        parsed_result = None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "user_id": row["user_id"],
        "payload": parsed_payload if isinstance(parsed_payload, dict) else {},
        "result": parsed_result if isinstance(parsed_result, dict) else parsed_result,
        "error": row["error"] or "",
        "attempts": row["attempts"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def public_job(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return {"job_id": 0, "status": "failed", "error": "not_found"}
    out: dict[str, Any] = {
        "job_id": job["id"],
        "status": job["status"],
        "kind": job["kind"],
    }
    if job["status"] == "done":
        out["result"] = job.get("result") or {}
    if job["status"] == "failed":
        out["error"] = job.get("error") or "ocr_fail"
    return out


def queued_count() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued', 'running')"
        ).fetchone()
    return int(row["n"] if row else 0)


def count_recent_jobs(user_id: int, kind: str, seconds: int = 60) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM jobs
            WHERE user_id = ? AND kind = ? AND created_at >= ?
            """,
            (user_id, kind, cutoff),
        ).fetchone()
    return int(row["n"] if row else 0)


def enqueue_job(kind: str, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if kind not in JOB_KINDS:
        raise ValueError("invalid_job_kind")
    if queued_count() >= max_queued_jobs():
        raise RuntimeError("scan_queue_full")
    limit = scan_rate_per_minute() if kind == "scan" else enrich_rate_per_minute()
    if count_recent_jobs(user_id, kind) >= limit:
        raise RuntimeError("scan_rate_limited" if kind == "scan" else "enrich_rate_limited")
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO jobs (kind, status, user_id, payload, created_at)
            VALUES (?, 'queued', ?, ?, ?)
            """,
            (kind, user_id, json.dumps(payload, ensure_ascii=False), now),
        )
        job_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    job = _job_from_row(row)
    if not job:
        raise RuntimeError("ocr_fail")
    return job


def store_scan_upload(jpeg: bytes) -> str:
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{os.urandom(4).hex()}.jpg"
    dest = get_jobs_dir() / name
    dest.write_bytes(jpeg)
    return f"jobs/{name}"


def resolve_job_file(relative: str) -> Path | None:
    raw = (relative or "").replace("\\", "/").strip()
    if not raw.startswith("jobs/") or ".." in raw.split("/"):
        return None
    path = (get_db_path().parent / raw).resolve()
    try:
        path.relative_to(get_jobs_dir().resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def get_job(job_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    with connect() as conn:
        if user_id is None:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jobs WHERE id = ? AND user_id = ?",
                (job_id, user_id),
            ).fetchone()
    return _job_from_row(row)


def claim_job() -> dict[str, Any] | None:
    conn = _immediate()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None
        now = _now()
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = ?, attempts = attempts + 1
            WHERE id = ? AND status = 'queued'
            """,
            (now, row["id"]),
        )
        if cur.rowcount != 1:
            conn.execute("COMMIT")
            return None
        claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        conn.execute("COMMIT")
        return _job_from_row(claimed)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def complete_job(job_id: int, result: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'done', result = ?, error = '', finished_at = ?
            WHERE id = ?
            """,
            (json.dumps(result, ensure_ascii=False, default=str), _now(), job_id),
        )


def fail_job(job_id: int, error: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'failed', error = ?, finished_at = ?
            WHERE id = ?
            """,
            ((error or "ocr_fail")[:80], _now(), job_id),
        )


def requeue_job(job_id: int) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'queued', started_at = NULL
            WHERE id = ? AND status = 'running'
            """,
            (job_id,),
        )


def acquire_gemini_slot(job_id: int, timeout_sec: float = 90.0) -> bool:
    deadline = time.time() + max(0.2, timeout_sec)
    while time.time() < deadline:
        conn = _immediate()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM gemini_slots WHERE job_id IS NULL ORDER BY id LIMIT 1"
            ).fetchone()
            if row:
                cur = conn.execute(
                    """
                    UPDATE gemini_slots
                    SET job_id = ?, taken_at = ?
                    WHERE id = ? AND job_id IS NULL
                    """,
                    (job_id, _now(), row["id"]),
                )
                conn.execute("COMMIT")
                if cur.rowcount == 1:
                    return True
            else:
                conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        finally:
            conn.close()
        time.sleep(0.2)
    return False


def release_gemini_slot(job_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE gemini_slots SET job_id = NULL, taken_at = NULL WHERE job_id = ?",
            (job_id,),
        )


def lookup_cache_get(cache_key: str) -> dict[str, Any] | None:
    key = (cache_key or "").strip()
    if not key:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM product_lookup_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["payload"])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def lookup_cache_set(cache_key: str, payload: dict[str, Any]) -> None:
    key = (cache_key or "").strip()
    if not key:
        return
    stored = {
        name: value
        for name, value in (payload or {}).items()
        if not str(name).startswith("_")
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO product_lookup_cache (cache_key, payload, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, created_at = excluded.created_at
            """,
            (key, json.dumps(stored, ensure_ascii=False, default=str), _now()),
        )


def beat(worker_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO worker_heartbeats (worker_id, seen_at)
            VALUES (?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET seen_at = excluded.seen_at
            """,
            (worker_id, _now()),
        )


def workers_alive(within_sec: int = 20) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=within_sec)).isoformat(timespec="seconds")
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM worker_heartbeats WHERE seen_at >= ?",
            (cutoff,),
        ).fetchone()
    return int(row["n"] if row else 0)


def queue_stats() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
    counts = {row["status"]: int(row["n"]) for row in rows}
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "workers": workers_alive(),
    }


def _parse_iso(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def reap_stale_jobs() -> None:
    timeout = timedelta(seconds=job_timeout_sec())
    now = datetime.now(timezone.utc)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, attempts, started_at FROM jobs WHERE status = 'running'"
        ).fetchall()
        for row in rows:
            started = _parse_iso(row["started_at"])
            if started is None:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if now - started < timeout:
                continue
            job_id = int(row["id"])
            conn.execute(
                "UPDATE gemini_slots SET job_id = NULL, taken_at = NULL WHERE job_id = ?",
                (job_id,),
            )
            if int(row["attempts"] or 0) < 2:
                conn.execute(
                    """
                    UPDATE jobs SET status = 'queued', started_at = NULL
                    WHERE id = ? AND status = 'running'
                    """,
                    (job_id,),
                )
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'failed', error = 'scan_timeout', finished_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (_now(), job_id),
                )
        stale_slots = conn.execute(
            "SELECT id, taken_at FROM gemini_slots WHERE job_id IS NOT NULL"
        ).fetchall()
        for slot in stale_slots:
            taken = _parse_iso(slot["taken_at"])
            if taken is None:
                continue
            if taken.tzinfo is None:
                taken = taken.replace(tzinfo=timezone.utc)
            if now - taken >= timeout:
                conn.execute(
                    "UPDATE gemini_slots SET job_id = NULL, taken_at = NULL WHERE id = ?",
                    (slot["id"],),
                )


def cleanup_old_jobs() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, payload FROM jobs
            WHERE status IN ('done', 'failed') AND COALESCE(finished_at, created_at) < ?
            """,
            (cutoff,),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        for row in rows:
            try:
                payload = json.loads(row["payload"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            relative = str((payload or {}).get("image_path") or "")
            path = resolve_job_file(relative)
            if path:
                try:
                    path.unlink()
                except OSError:
                    pass
        if ids:
            conn.execute(
                f"DELETE FROM jobs WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )


def cleanup_job_file(job: dict[str, Any]) -> None:
    relative = str((job.get("payload") or {}).get("image_path") or "")
    path = resolve_job_file(relative)
    if path:
        try:
            path.unlink()
        except OSError:
            pass


def _error_code(exc: BaseException, kind: str) -> str:
    text = str(exc).strip()
    known = {
        "ocr_fail",
        "ocr_missing",
        "ocr_empty",
        "scan_timeout",
        "scan_rate_limited",
        "scan_queue_full",
        "enrich_fail",
        "enrich_rate_limited",
        "not_found",
        "name_roaster_required",
        "empty_image",
    }
    if text in known:
        return text
    return "enrich_fail" if kind == "enrich" else "ocr_fail"


def execute_job(job: dict[str, Any]) -> dict[str, Any]:
    kind = job.get("kind")
    payload = job.get("payload") or {}
    if kind == "scan":
        from routes.scan import process_scan_jpeg

        path = resolve_job_file(str(payload.get("image_path") or ""))
        if not path:
            raise RuntimeError("ocr_fail")
        jpeg = path.read_bytes()
        if not jpeg:
            raise RuntimeError("empty_image")
        lang = str(payload.get("lang") or "da")
        return process_scan_jpeg(jpeg, lang)
    if kind == "enrich":
        from routes.beans import process_enrich_job

        return process_enrich_job(payload, int(job.get("user_id") or 0))
    raise RuntimeError("ocr_fail")


def handle_job(job: dict[str, Any]) -> None:
    job_id = int(job["id"])
    kind = str(job.get("kind") or "scan")
    slot_timeout = 30.0 if kind == "enrich" else 90.0
    if not acquire_gemini_slot(job_id, timeout_sec=slot_timeout):
        requeue_job(job_id)
        return
    try:
        result = execute_job(job)
        complete_job(job_id, result)
    except Exception as exc:
        print(f"job {job_id} {kind} failed: {exc}")
        fail_job(job_id, _error_code(exc, kind))
    finally:
        release_gemini_slot(job_id)
        cleanup_job_file(job)


_cleanup_tick = 0
_cleanup_lock = threading.Lock()


def worker_tick(worker_id: str) -> bool:
    """Claim and run at most one job. Returns True if a job ran."""
    global _cleanup_tick
    beat(worker_id)
    reap_stale_jobs()
    with _cleanup_lock:
        _cleanup_tick += 1
        if _cleanup_tick % 50 == 0:
            cleanup_old_jobs()
    job = claim_job()
    if not job:
        return False
    handle_job(job)
    return True
