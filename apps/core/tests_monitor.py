from django.core.cache import cache

from apps.core.perf import norm_path, record, request_stats
from apps.core.tests_phase2 import Base, PW
from apps.accounts.models import User


class MonitorTests(Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.root = User.objects.create_superuser("root_mon", password=PW)

    def test_only_super_admin_sees_the_monitor(self):
        self.assertEqual(self.api(self.sa).get("/api/system/monitor/").status_code, 403)
        r = self.api(self.root).get("/api/system/monitor/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("db", r.data)
        self.assertIn("requests", r.data)

    def test_request_timing_buckets_and_slow_list(self):
        record("/api/exams/12/start/", 200, 120)
        record("/api/exams/13/start/", 200, 2000)
        stats = request_stats()
        self.assertGreaterEqual(stats["requests"], 2)
        self.assertGreaterEqual(stats["slow"], 1)
        self.assertEqual(stats["recent_slow"][0]["path"], "/api/exams/{id}/start/")

    def test_paths_are_normalised(self):
        self.assertEqual(norm_path("/api/attempts/77/answer/"), "/api/attempts/{id}/answer/")
