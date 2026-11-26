from apps.academics.models import ClassRoom
from apps.accounts.models import User
from apps.core.tests_phase2 import Base, PW
from apps.core.models import AuditLog  # noqa: F401


class ScopedReportTests(Base):
    def setUp(self):
        super().setUp()
        self.assess = User.objects.create_user("exp_r", password=PW, role="expert", org=self.reg, expert_kind="assessment")

    def test_catalogue_by_role(self):
        self.assertEqual({r["key"] for r in self.api(self.assess).get("/api/reports/catalog/").data},
                         {"district", "fields", "classes", "teachers", "students", "grades", "student_grades"})
        self.assertEqual(self.api(self.t1).get("/api/reports/catalog/").data, [])

    def test_region_expert_gets_district_report_for_own_region(self):
        r = self.api(self.assess).get("/api/reports/run/district/")
        self.assertEqual(r.status_code, 200)
        names = {row["school"] for row in r.data["rows"]}
        self.assertEqual(names, {self.school.name, self.school2.name})
        self.assertEqual(r.data["totals"]["students"], 3)            # s1, s2, s3 in school A

    def test_school_report_requires_school_in_scope(self):
        self.assertEqual(self.api(self.sa).get(f"/api/reports/run/classes/?org={self.school.pk}").status_code, 200)
        self.assertEqual(self.api(self.sa).get(f"/api/reports/run/classes/?org={self.school2.pk}").status_code, 404)
        self.assertEqual(self.api(self.sa).get("/api/reports/run/district/").status_code, 403)

    def test_teacher_has_no_reports_and_csv_is_safe(self):
        self.assertEqual(self.api(self.t1).get(f"/api/reports/run/classes/?org={self.school.pk}").status_code, 403)
        r = self.api(self.sa).get(f"/api/reports/run/students/?org={self.school.pk}&format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])

    def test_class_report_matches_class_school(self):
        other = ClassRoom.objects.create(school=self.school2, academic_year=self.year, name="ب", grade=self.grade)
        r = self.api(self.sa).get(f"/api/reports/run/classes/?org={self.school.pk}&classroom={other.pk}")
        self.assertEqual(r.status_code, 404)
