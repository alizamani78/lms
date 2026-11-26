from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import OrgUnit, User
from apps.courses.models import Course, Enrollment
from apps.exams.models import Answer, Attempt, Choice, Exam, Question

PW = "Str0ng-pass-1"


class ReportTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.prov = OrgUnit.objects.create(name="P", kind="province")
        self.reg = OrgUnit.objects.create(name="R", kind="region", parent=self.prov)
        self.reg2 = OrgUnit.objects.create(name="R2", kind="region", parent=self.prov)
        self.school = OrgUnit.objects.create(name="A", kind="school", parent=self.reg)
        self.school2 = OrgUnit.objects.create(name="B", kind="school", parent=self.reg2)
        self.t = User.objects.create_user("t1", password=PW, role="teacher", org=self.school)
        self.t2 = User.objects.create_user("t2", password=PW, role="teacher", org=self.school2)
        self.radmin = User.objects.create_user("ra", password=PW, role="region_admin", org=self.reg)
        self.padmin = User.objects.create_user("pa", password=PW, role="province_admin", org=self.prov)
        self.s1 = User.objects.create_user("=cmd", password=PW, role="student", org=self.school, last_name="@x")
        self.s2 = User.objects.create_user("s2", password=PW, role="student", org=self.school)
        course = Course.objects.create(org=self.school, title="ریاضی", teacher=self.t)
        now = timezone.now()
        self.exam = Exam.objects.create(course=course, title="e", starts_at=now, ends_at=now + timedelta(hours=1),
                                        is_published=True)
        self.q = Question.objects.create(exam=self.exam, text="q")
        self.ok = Choice.objects.create(question=self.q, text="a", is_correct=True)
        self.no = Choice.objects.create(question=self.q, text="b")
        for st, ch, score in ((self.s1, self.ok, 1), (self.s2, self.no, 0)):
            Enrollment.objects.create(course=course, student=st)
            a = Attempt.objects.create(exam=self.exam, student=st, deadline=now, submitted_at=now, score=score, total=1)
            Answer.objects.create(attempt=a, question=self.q, choice=ch, answered_at=now)

    def c(self, user):
        cl = APIClient()
        r = cl.post("/api/auth/login/", {"username": user.username, "password": PW}, format="json")
        cl.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return cl

    def test_exam_report_numbers(self):
        d = self.c(self.t).get(f"/api/reports/exams/{self.exam.pk}/").data
        self.assertEqual((d["enrolled"], d["started"], d["submitted"]), (2, 2, 2))
        self.assertEqual(d["avg_percent"], 50.0)
        self.assertEqual(d["histogram_10pct_buckets"][0], 1)
        self.assertEqual(d["histogram_10pct_buckets"][9], 1)
        self.assertEqual(d["questions"][0]["correct_rate"], 50.0)

    def test_report_scope(self):
        url = f"/api/reports/exams/{self.exam.pk}/"
        self.assertEqual(self.c(self.t2).get(url).status_code, 404)
        self.assertEqual(self.c(self.s1).get(url).status_code, 403)
        self.assertEqual(self.c(self.radmin).get(url).status_code, 200)

    def test_csv_formula_injection_neutralised(self):
        r = self.c(self.t).get(f"/api/reports/exams/{self.exam.pk}/results.csv")
        body = b"".join(r.streaming_content).decode("utf-8-sig")
        self.assertIn("'=cmd", body)
        self.assertIn("'@x", body)

    def test_org_summary(self):
        d = self.c(self.padmin).get(f"/api/reports/orgs/{self.prov.pk}/").data
        by = {c["name"]: c for c in d["children"]}
        self.assertEqual(by["R"]["students"], 2)
        self.assertEqual(by["R"]["avg_percent"], 50.0)
        self.assertEqual(by["R2"]["students"], 0)
        # a region admin cannot read another region or the province
        self.assertEqual(self.c(self.radmin).get(f"/api/reports/orgs/{self.reg2.pk}/").status_code, 404)
        self.assertEqual(self.c(self.radmin).get(f"/api/reports/orgs/{self.prov.pk}/").status_code, 404)
        self.assertEqual(self.c(self.t).get(f"/api/reports/orgs/{self.reg.pk}/").status_code, 403)
