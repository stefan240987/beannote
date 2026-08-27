"""Job queue: atomic claim, rate limits, and worker dispatch off the event loop."""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["JOB_WORKER_EMBEDDED"] = "0"
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ["RESET_DB_ON_START"] = "false"

from db import create_email_user, init_db
from jobs import (
    acquire_gemini_slot,
    claim_job,
    enqueue_job,
    get_job,
    handle_job,
    lookup_cache_get,
    lookup_cache_set,
    public_job,
    release_gemini_slot,
    store_scan_upload,
)


class JobQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "beannote.db"
        self._env = {
            "BEANNOTE_DB_PATH": os.environ.get("BEANNOTE_DB_PATH"),
            "OCR_MAX_CONCURRENT": os.environ.get("OCR_MAX_CONCURRENT"),
            "SCAN_RATE_PER_MINUTE": os.environ.get("SCAN_RATE_PER_MINUTE"),
            "ENRICH_RATE_PER_MINUTE": os.environ.get("ENRICH_RATE_PER_MINUTE"),
            "MAX_QUEUED_JOBS": os.environ.get("MAX_QUEUED_JOBS"),
        }
        os.environ["BEANNOTE_DB_PATH"] = str(self.db_path)
        os.environ["OCR_MAX_CONCURRENT"] = "1"
        os.environ["SCAN_RATE_PER_MINUTE"] = "3"
        os.environ["ENRICH_RATE_PER_MINUTE"] = "2"
        os.environ["MAX_QUEUED_JOBS"] = "10"
        init_db()
        self.user = create_email_user("queue@beannote.test", "password1")
        self.other = create_email_user("other@beannote.test", "password1")

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_two_workers_cannot_claim_the_same_job(self):
        first = enqueue_job("scan", self.user["id"], {"lang": "da"})
        second = enqueue_job("scan", self.user["id"], {"lang": "da"})
        claimed: list[int] = []
        barrier = threading.Barrier(2)

        def _claim() -> None:
            barrier.wait()
            job = claim_job()
            if job:
                claimed.append(int(job["id"]))

        threads = [threading.Thread(target=_claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(claimed), sorted([first["id"], second["id"]]))
        self.assertIsNone(claim_job())

    def test_scan_rate_limit(self):
        for _ in range(3):
            enqueue_job("scan", self.user["id"], {"lang": "da"})
        with self.assertRaises(RuntimeError) as raised:
            enqueue_job("scan", self.user["id"], {"lang": "da"})
        self.assertEqual(str(raised.exception), "scan_rate_limited")
        enqueue_job("scan", self.other["id"], {"lang": "da"})

    def test_job_is_private_to_owner(self):
        job = enqueue_job("scan", self.user["id"], {"lang": "da"})
        self.assertIsNotNone(get_job(job["id"], user_id=self.user["id"]))
        self.assertIsNone(get_job(job["id"], user_id=self.other["id"]))

    def test_public_job_hides_result_until_done(self):
        job = enqueue_job("scan", self.user["id"], {"lang": "da"})
        public = public_job(job)
        self.assertEqual(public["status"], "queued")
        self.assertNotIn("result", public)
        self.assertEqual(public["job_id"], job["id"])

    def test_lookup_cache_roundtrip(self):
        lookup_cache_set("uno\x1fcr\x1fda", {"bean_name": "Uno", "_source_html": "<huge>"})
        hit = lookup_cache_get("uno\x1fcr\x1fda")
        self.assertEqual(hit["bean_name"], "Uno")
        self.assertNotIn("_source_html", hit)

    def test_gemini_slot_is_exclusive(self):
        self.assertTrue(acquire_gemini_slot(11, timeout_sec=0.5))
        self.assertFalse(acquire_gemini_slot(12, timeout_sec=0.4))
        release_gemini_slot(11)
        self.assertTrue(acquire_gemini_slot(12, timeout_sec=0.5))
        release_gemini_slot(12)

    def test_scan_http_returns_before_ocr_finishes(self):
        from io import BytesIO

        from fastapi.testclient import TestClient
        from PIL import Image

        os.environ["JOB_WORKER_EMBEDDED"] = "1"
        from main import app

        buf = BytesIO()
        Image.new("RGB", (12, 12), (60, 42, 33)).save(buf, format="JPEG")
        jpeg = buf.getvalue()

        def _slow_scan(_jpeg: bytes, lang: str) -> dict:
            time.sleep(1.6)
            return {"name": "Uno", "roaster": "CR", "lang": lang}

        with (
            patch("ocr.scan_available", return_value=True),
            patch("routes.scan.process_scan_jpeg", side_effect=_slow_scan),
            TestClient(app) as client,
        ):
            auth = client.post(
                "/api/auth/register",
                json={"email": "scanhttp@beannote.test", "password": "password1"},
            )
            token = auth.json()["token"]
            headers = {"Authorization": f"Bearer {token}"}
            started = time.perf_counter()
            resp = client.post(
                "/api/scan",
                headers=headers,
                files={"file": ("bag.jpg", jpeg, "image/jpeg")},
                data={"lang": "da"},
            )
            elapsed = time.perf_counter() - started
            self.assertEqual(resp.status_code, 202, resp.text)
            self.assertIn("job_id", resp.json())
            self.assertLess(elapsed, 1.0)
            health_started = time.perf_counter()
            health = client.get("/api/health")
            health_elapsed = time.perf_counter() - health_started
            self.assertEqual(health.status_code, 200)
            self.assertLess(health_elapsed, 0.8)
            self.assertIn("jobs", health.json())
        os.environ["JOB_WORKER_EMBEDDED"] = "0"
        image_path = store_scan_upload(b"fake-jpeg")
        job = enqueue_job("scan", self.user["id"], {"image_path": image_path, "lang": "da"})
        with patch("routes.scan.process_scan_jpeg", return_value={"name": "Uno", "roaster": "CR"}) as mocked:
            handle_job(job)
            mocked.assert_called_once()
        done = get_job(job["id"], user_id=self.user["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["result"]["name"], "Uno")
        self.assertFalse(Path(self.db_path.parent / image_path).exists())


if __name__ == "__main__":
    unittest.main()
