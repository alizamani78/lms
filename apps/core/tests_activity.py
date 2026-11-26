from apps.accounts.models import User
from django.core.cache import cache
from django.test import override_settings

from apps.core.models import AuditLog
from apps.core.tests_phase2 import Base, PW


class ActivityTrailTests(Base):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_login_and_failed_login_are_recorded(self):
        from rest_framework.test import APIClient
        c = APIClient()
        c.post("/api/auth/login/", {"username": "s1", "password": PW}, format="json")
        c.post("/api/auth/login/", {"username": "s1", "password": "wrong-pass"}, format="json")
        self.assertTrue(AuditLog.objects.filter(action="auth.login", user=self.s1).exists())
        self.assertTrue(AuditLog.objects.filter(action="auth.login_failed", target="s1").exists())

    def test_task_view_recorded_once_per_window(self):
        self.api(self.s1).get("/api/assignments/")
        self.api(self.s1).get("/api/assignments/")
        self.assertEqual(AuditLog.objects.filter(action="assignment.view", user=self.s1).count(), 1)

    def test_activity_scope(self):
        AuditLog.objects.create(user=self.s1, action="exam.start", target="exam:1")
        root = User.objects.create_superuser("root_act", password=PW)
        self.assertGreaterEqual(len(self.api(root).get("/api/activity/").data["rows"]), 1)
        rows = self.api(self.sa).get("/api/activity/").data["rows"]
        self.assertTrue(all(r["username"] != "" for r in rows))
        self.assertEqual(self.api(self.t1).get("/api/activity/").status_code, 403)
