"""ADMIN_EMAIL / ADMIN_PASSWORD create and update the Unraid admin account."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ENVIRONMENT", "production")
os.environ["RESET_DB_ON_START"] = "false"
os.environ["JWT_SECRET"] = "test-admin-env-secret"


class AdminEnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._env = {
            "BEANNOTE_DB_PATH": os.environ.get("BEANNOTE_DB_PATH"),
            "ADMIN_EMAIL": os.environ.get("ADMIN_EMAIL"),
            "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD"),
        }
        os.environ["BEANNOTE_DB_PATH"] = str(Path(self.tmp.name) / "beannote.db")
        os.environ.pop("ADMIN_EMAIL", None)
        os.environ.pop("ADMIN_PASSWORD", None)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _init(self):
        from db import init_db

        init_db()

    def test_creates_admin_from_env(self):
        os.environ["ADMIN_EMAIL"] = "stefan@example.com"
        os.environ["ADMIN_PASSWORD"] = "unraidpass"
        self._init()
        from db import authenticate_email, get_user_by_email

        row = get_user_by_email("stefan@example.com")
        self.assertTrue(row)
        self.assertEqual(row["is_admin"], 1)
        user = authenticate_email("stefan@example.com", "unraidpass")
        self.assertTrue(user)
        self.assertTrue(user["is_admin"])

    def test_updates_password_and_keeps_admin(self):
        os.environ["ADMIN_EMAIL"] = "stefan@example.com"
        os.environ["ADMIN_PASSWORD"] = "firstpass"
        self._init()
        os.environ["ADMIN_PASSWORD"] = "secondpass"
        self._init()
        from db import authenticate_email

        self.assertIsNone(authenticate_email("stefan@example.com", "firstpass"))
        user = authenticate_email("stefan@example.com", "secondpass")
        self.assertTrue(user and user["is_admin"])

    def test_short_password_does_not_create(self):
        os.environ["ADMIN_EMAIL"] = "stefan@example.com"
        os.environ["ADMIN_PASSWORD"] = "short"
        self._init()
        from db import get_user_by_email

        self.assertIsNone(get_user_by_email("stefan@example.com"))

    def test_email_only_promotes_existing_user(self):
        self._init()
        from db import create_email_user, get_user_by_email

        user = create_email_user("stefan@example.com", "password1")
        self.assertFalse(user["is_admin"])
        os.environ["ADMIN_EMAIL"] = "stefan@example.com"
        self._init()
        row = get_user_by_email("stefan@example.com")
        self.assertEqual(row["is_admin"], 1)


if __name__ == "__main__":
    unittest.main()
