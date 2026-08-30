"""Admin analytics APIs require an admin session; guests and members are rejected."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ENVIRONMENT", "dev")
os.environ["RESET_DB_ON_START"] = "false"
os.environ["JWT_SECRET"] = "test-admin-analytics-secret"


class AdminAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["BEANNOTE_DB_PATH"] = str(Path(cls.tmp.name) / "beannote.db")
        from fastapi.testclient import TestClient

        import db
        import main

        db.init_db()
        cls.admin = db.create_email_user("admin@beannote.test", "password1", "Admin")
        with db.connect() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (cls.admin["id"],))
        cls.admin = db.get_user(cls.admin["id"])
        cls.member = db.create_email_user("member@beannote.test", "password1", "Member")
        cls._cm = TestClient(main.app)
        cls.client = cls._cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._cm.__exit__(None, None, None)
        cls.tmp.cleanup()

    def _login(self, email: str, password: str = "password1"):
        res = self.client.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return res

    def test_guest_admin_api_is_401(self):
        self.client.cookies.clear()
        res = self.client.get("/api/admin/analytics")
        self.assertEqual(res.status_code, 401)

    def test_member_admin_api_is_403(self):
        self._login("member@beannote.test")
        res = self.client.get("/api/admin/analytics")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json().get("detail"), "forbidden")
        self.client.cookies.clear()

    def test_admin_analytics_and_users(self):
        from db import record_api_hit, record_pageview

        record_pageview("/explore", visitor_id="v1", session_id="s1")
        record_pageview("/diary", visitor_id="v1", session_id="s1", user_id=self.member["id"])
        record_api_hit("/api/beans", 401)
        record_api_hit("/api/beans", 200)
        self._login("admin@beannote.test")
        res = self.client.get("/api/admin/analytics?days=30")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertGreaterEqual(body["users"]["total"], 2)
        self.assertIn("dau", body["users"])
        self.assertIn("mau", body["users"])
        self.assertIn("signups", body["users"])
        self.assertGreaterEqual(body["traffic"]["pageviews"], 2)
        self.assertGreaterEqual(body["traffic"]["groups"]["explore"], 1)
        self.assertGreaterEqual(body["traffic"]["groups"]["internal"], 1)
        self.assertGreaterEqual(body["health"]["status_401"], 1)
        users = self.client.get("/api/admin/users?q=member")
        self.assertEqual(users.status_code, 200)
        items = users.json()["items"]
        self.assertTrue(any(row["email"] == "member@beannote.test" for row in items))
        self.client.cookies.clear()

    def test_admin_html_redirects_non_admin(self):
        self.client.cookies.clear()
        guest = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(guest.status_code, 303)
        self.assertIn("/login", guest.headers.get("location", ""))
        self._login("member@beannote.test")
        member = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(member.status_code, 303)
        self.assertEqual(member.headers.get("location"), "/explore")
        self.client.cookies.clear()
        self._login("admin@beannote.test")
        admin = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(admin.status_code, 200)
        self.assertIn("BeanNote", admin.text)
        self.client.cookies.clear()

    def test_block_and_blocked_login(self):
        from db import create_email_user, get_user

        extra = create_email_user("blockme@beannote.test", "password1", "Blockme")
        self._login("admin@beannote.test")
        blocked = self.client.post(f"/api/admin/users/{extra['id']}/block", json={})
        self.assertEqual(blocked.status_code, 200)
        self.assertTrue(get_user(extra["id"])["is_blocked"])
        self_block = self.client.post(f"/api/admin/users/{self.admin['id']}/block", json={})
        self.assertEqual(self_block.status_code, 400)
        self.client.cookies.clear()
        login = self.client.post(
            "/api/auth/login",
            json={"email": "blockme@beannote.test", "password": "password1"},
        )
        self.assertEqual(login.status_code, 403)
        self.assertEqual(login.json().get("detail"), "account_blocked")
        self._login("admin@beannote.test")
        opened = self.client.post(f"/api/admin/users/{extra['id']}/unblock", json={})
        self.assertEqual(opened.status_code, 200)
        self.assertFalse(get_user(extra["id"])["is_blocked"])
        self.client.cookies.clear()

    def test_pageview_is_public(self):
        self.client.cookies.clear()
        res = self.client.post("/api/analytics/pageview", json={"path": "/explore"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("ok"))

    def test_admin_activity_is_excluded_from_stats(self):
        from db import admin_analytics, record_api_hit, record_pageview

        record_pageview("/explore", visitor_id="guest-v", session_id="guest-s")
        before = admin_analytics(30)
        record_pageview(
            "/explore",
            visitor_id="admin-v",
            session_id="admin-s",
            user_id=self.admin["id"],
        )
        record_api_hit("/api/beans", 500, is_admin=True)
        record_api_hit("/api/admin/analytics", 200)
        after = admin_analytics(30)
        self.assertEqual(after["traffic"]["pageviews"], before["traffic"]["pageviews"])
        self.assertEqual(after["health"]["status_500"], before["health"]["status_500"])
        self.assertEqual(after["health"]["api_total"], before["health"]["api_total"])
        self._login("admin@beannote.test")
        ping = self.client.post("/api/analytics/pageview", json={"path": "/diary"})
        self.assertEqual(ping.status_code, 200)
        live = self.client.get("/api/admin/analytics?days=30").json()
        self.assertEqual(live["traffic"]["pageviews"], before["traffic"]["pageviews"])
        self.assertLessEqual(live["users"]["dau"], before["users"]["dau"] + 1)
        self.client.cookies.clear()
        record_pageview("/diary", visitor_id="member-v", session_id="member-s", user_id=self.member["id"])
        grown = admin_analytics(30)
        self.assertEqual(grown["traffic"]["pageviews"], before["traffic"]["pageviews"] + 1)

    def test_member_cannot_enrich_bean(self):
        from db import insert_bean

        created = insert_bean("Audit Enrich Bean", "Audit Roaster", skip_fuzzy=True)
        bean_id = created["bean"]["id"]
        self._login("member@beannote.test")
        res = self.client.post(f"/api/beans/{bean_id}/enrich?lang=da", json={})
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json().get("detail"), "forbidden")
        self.client.cookies.clear()

    def test_admin_near_match_queue_keep_and_delete(self):
        from db import insert_bean

        insert_bean("Uno", "Risteriet Coffee")
        created = insert_bean("Uno Espresso", "Risteriet Coffee", skip_fuzzy=True)
        self.assertEqual(created["status"], "created")
        self._login("member@beannote.test")
        denied = self.client.get("/api/admin/beans/near-matches")
        self.assertEqual(denied.status_code, 403)
        self.client.cookies.clear()
        self._login("admin@beannote.test")
        queued = self.client.get("/api/admin/beans/near-matches")
        self.assertEqual(queued.status_code, 200)
        rows = queued.json()
        self.assertTrue(any(row["bean_id"] == created["bean"]["id"] for row in rows))
        review_id = next(row["id"] for row in rows if row["bean_id"] == created["bean"]["id"])
        stats = self.client.get("/api/admin/analytics?days=30").json()
        self.assertGreaterEqual(stats["content"]["near_reviews"], 1)
        kept = self.client.post(f"/api/admin/beans/near-matches/{review_id}/keep")
        self.assertEqual(kept.status_code, 200)
        after_keep = self.client.get("/api/admin/beans/near-matches").json()
        self.assertFalse(any(row["bean_id"] == created["bean"]["id"] for row in after_keep))
        deleted = self.client.delete(f"/api/admin/beans/{created['bean']['id']}")
        self.assertEqual(deleted.status_code, 200)
        missing = self.client.delete(f"/api/admin/beans/{created['bean']['id']}")
        self.assertEqual(missing.status_code, 404)
        self.client.cookies.clear()


if __name__ == "__main__":
    unittest.main()
