"""Unraid/Docker helpers: cookies, catalog persistence, production env writes."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ["RESET_DB_ON_START"] = "false"
os.environ.setdefault("JWT_SECRET", "test-unraid-secret")


class CookieSecureTests(unittest.TestCase):
    def test_local_dev_is_never_secure(self):
        import deps

        with patch.object(deps, "ENVIRONMENT", "dev"):
            req = Mock()
            req.headers.get.return_value = "https"
            req.url.scheme = "https"
            self.assertFalse(deps._cookie_secure(req))

    def test_production_lan_http_is_not_secure(self):
        import deps

        with patch.object(deps, "ENVIRONMENT", "production"):
            with patch.dict(os.environ, {"PUBLIC_BASE_URL": ""}, clear=False):
                os.environ.pop("PUBLIC_BASE_URL", None)
                req = Mock()
                req.headers.get.return_value = ""
                req.url.scheme = "http"
                self.assertFalse(deps._cookie_secure(req))

    def test_production_forwarded_https_is_secure(self):
        import deps

        with patch.object(deps, "ENVIRONMENT", "production"):
            with patch.dict(os.environ, {"PUBLIC_BASE_URL": ""}, clear=False):
                os.environ.pop("PUBLIC_BASE_URL", None)
                req = Mock()
                req.headers.get.return_value = "https"
                req.url.scheme = "http"
                self.assertTrue(deps._cookie_secure(req))

    def test_public_https_base_forces_secure(self):
        import deps

        with patch.object(deps, "ENVIRONMENT", "production"):
            with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://beannote.example.com"}):
                self.assertTrue(deps._cookie_secure(None))


class CatalogPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = os.environ.get("BEANNOTE_DB_PATH")
        os.environ["BEANNOTE_DB_PATH"] = str(Path(self.tmp.name) / "beannote.db")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("BEANNOTE_DB_PATH", None)
        else:
            os.environ["BEANNOTE_DB_PATH"] = self._env
        self.tmp.cleanup()

    def test_volume_catalog_wins_over_packaged(self):
        from db import get_catalog_dir, resolve_catalog_image

        volume = get_catalog_dir("beans") / "unraid-probe.jpg"
        volume.write_bytes(b"volume")
        found = resolve_catalog_image("beans", "unraid-probe.jpg")
        self.assertEqual(found, volume)
        self.assertEqual(found.read_bytes(), b"volume")

    def test_falls_back_to_packaged_placeholder(self):
        from db import resolve_catalog_image

        found = resolve_catalog_image("gear", "placeholder.svg")
        self.assertIsNotNone(found)
        self.assertTrue(found.is_file())


class ProductionEnvWriteTests(unittest.TestCase):
    def test_production_does_not_write_dotenv(self):
        import ocr

        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp)
            with patch.object(ocr, "_project_root", return_value=fake_root):
                with patch.dict(os.environ, {"ENVIRONMENT": "production", "GEMINI_API_KEY": "abc"}):
                    ocr.ensure_local_env()
            self.assertFalse((fake_root / ".env").exists())
            self.assertFalse((fake_root / ".streamlit" / "secrets.toml").exists())

    def test_upload_cap_rejects_huge_payload(self):
        import ocr

        with patch.object(ocr, "max_upload_bytes", return_value=8):
            with self.assertRaises(ValueError) as ctx:
                ocr.assert_upload_size(b"0123456789")
            self.assertEqual(str(ctx.exception), "upload_too_large")
        ocr.assert_upload_size(b"small")


class AppSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["BEANNOTE_DB_PATH"] = str(Path(cls.tmp.name) / "beannote.db")
        os.environ["JWT_SECRET"] = "test-unraid-secret"
        os.environ["RESET_DB_ON_START"] = "false"
        from fastapi.testclient import TestClient

        import main
        from db import get_catalog_dir

        get_catalog_dir("beans").joinpath("overlay.jpg").write_bytes(b"from-volume")
        cls._cm = TestClient(main.app)
        cls.client = cls._cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        cls.tmp.cleanup()

    def test_packaged_gear_placeholder_still_served(self):
        res = self.client.get("/static/img/gear/placeholder.svg")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"<svg", res.content)

    def test_volume_bean_photo_served_at_same_url(self):
        res = self.client.get("/static/img/beans/overlay.jpg")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"from-volume")

    def test_index_keeps_palette_tailwind_and_nav(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        body = res.text
        self.assertIn("cdn.tailwindcss.com", body)
        self.assertIn("#3c2a21", body)
        self.assertIn("#b85c38", body)
        self.assertIn("#faf6f0", body)
        self.assertIn("bottom-nav-tabs", body)
        self.assertIn("/static/css/styles.css", body)
        self.assertIn("/static/js/app.js", body)

    def test_health_and_config_still_ok(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json().get("ok"))
        config = self.client.get("/api/config")
        self.assertEqual(config.status_code, 200)
        self.assertIn("strings", config.json())


if __name__ == "__main__":
    unittest.main()
