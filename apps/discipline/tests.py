from decimal import Decimal
from io import StringIO

from django.core.management import call_command

from apps.accounts.models import OrgUnit, SchoolMembership, User
from apps.core.tests_phase2 import Base, PW
from apps.discipline.models import DisciplineFactor, DisciplineRecord


class DisciplineScoreTests(Base):
    def setUp(self):
        super().setUp()
        call_command("seed_discipline_defaults", stdout=StringIO())
        self.late = DisciplineFactor.objects.get(school=None, title="تأخیر در ورود به کلاس")
        self.unexcused = DisciplineFactor.objects.get(school=None, title="غیبت غیرموجه")
        self.exec = User.objects.create_user("exec_disc", password=PW, role="school_deputy_exec", org=self.school)
        SchoolMembership.objects.create(user=self.exec, school=self.school)

    def test_defaults_and_signs(self):
        self.assertEqual(self.late.points, Decimal("-0.50"))
        self.assertEqual(self.unexcused.points, Decimal("-1.00"))

    def test_record_copies_points_and_score_is_base_plus_points(self):
        r = self.api(self.t1).post("/api/discipline/records/", {"student": self.s1.pk, "factor": self.late.pk}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        self.api(self.t1).post("/api/discipline/records/", {"student": self.s1.pk, "factor": self.unexcused.pk}, format="json")
        ws = self.api(self.t1).get(f"/api/discipline/records/?student={self.s1.pk}").data
        self.assertEqual(ws["total_points"], -1.5)
        self.assertEqual(ws["score"], 18.5)
        self.assertEqual(len(ws["records"]), 2)

    def test_student_sees_own_worksheet_only(self):
        self.assertEqual(self.api(self.s1).get(f"/api/discipline/worksheet/{self.s1.pk}/").status_code, 200)
        self.assertIn(self.api(self.s1).get(f"/api/discipline/worksheet/{self.s2.pk}/").status_code, (403, 404))

    def test_school_factor_and_deputy_recording(self):
        r = self.api(self.exec).post("/api/discipline/factors/", {"school": self.school.pk, "title": "دیر آمدن به آزمایشگاه", "points": "-0.25"}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        fid = r.data["id"]
        rec = self.api(self.exec).post("/api/discipline/records/", {"student": self.s1.pk, "factor": fid}, format="json")
        self.assertEqual(rec.status_code, 201, getattr(rec, "data", ""))

    def test_default_catalogue_only_for_super_admin(self):
        self.assertEqual(self.api(self.t1).patch(f"/api/discipline/factors/{self.late.pk}/", {"points": "-1"}, format="json").status_code, 403)
        root = User.objects.create_superuser("root_disc", password=PW)
        self.assertEqual(self.api(root).patch(f"/api/discipline/factors/{self.late.pk}/", {"points": "-0.75"}, format="json").status_code, 200)

    def test_student_factor_outside_scope_is_refused(self):
        other = OrgUnit.objects.create(name="مدرسه دیگر", kind="school", parent=self.region)
        f = DisciplineFactor.objects.create(school=other, title="عامل مدرسه دیگر", points=Decimal("-1"))
        r = self.api(self.t1).post("/api/discipline/records/", {"student": self.s1.pk, "factor": f.pk}, format="json")
        self.assertEqual(r.status_code, 400)
