from apps.evaluation.ranking import competition_ranks
from apps.core.tests_phase2 import Base
from apps.exams.models import Attempt, Exam
from datetime import timedelta
from django.utils import timezone


class RankingTests(Base):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        ex = Exam.objects.create(course=self.course, title="e", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1), is_published=True)
        Attempt.objects.create(exam=ex, student=self.s1, deadline=now, submitted_at=now, score=18, total=20)   # 90
        Attempt.objects.create(exam=ex, student=self.s2, deadline=now, submitted_at=now, score=18, total=20)   # 90 (tie)
        Attempt.objects.create(exam=ex, student=self.s3, deadline=now, submitted_at=now, score=10, total=20)   # 50

    def test_competition_ranking_with_ties(self):
        self.assertEqual(competition_ranks({"a": 90, "b": 90, "c": 50, "d": None}), {"a": 1, "b": 1, "c": 3})

    def test_reports_have_class_school_region_province_ranks(self):
        r = self.api(self.sa).get(f"/api/reports/run/ranking_overall/?org={self.school.pk}")
        self.assertEqual(r.status_code, 200)
        row = r.data["rows"][0]
        self.assertEqual(row["school_rank"], 1)
        self.assertIn("region_rank", row)
        self.assertIn("province_rank", row)
        rc = self.api(self.sa).get(f"/api/reports/run/ranking_course/?org={self.school.pk}")
        self.assertEqual(rc.status_code, 200)
        self.assertTrue(all("class_rank" in x for x in rc.data["rows"]))
        d = self.api(self.ra).get("/api/reports/run/ranking_district/")
        self.assertEqual(d.status_code, 200)
