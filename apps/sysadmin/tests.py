from django.utils import timezone
from datetime import timedelta

from apps.academics.models import AcademicYear, ClassRoom, Grade, Level
from apps.accounts.models import OrgUnit, User
from apps.core.tests_phase2 import Base, PW


class SystemPanelTests(Base):
    def test_only_super_admin_reaches_the_panel_api(self):
        for u in (self.sa, self.ra, self.t1, self.s1):
            self.assertEqual(self.api(u).get("/api/system/schema/").status_code, 403)

    def test_schema_lists_sections_and_no_secret_fields(self):
        root = User.objects.create_superuser("root_sys", password=PW)
        sc = self.api(root).get("/api/system/schema/").data
        keys = {r["key"] for r in sc}
        self.assertTrue({"users", "schools", "classes", "subjects", "years", "levels"} <= keys)
        users = next(r for r in sc if r["key"] == "users")
        names = {f["name"] for f in users["fields"]}
        self.assertNotIn("password", names)
        self.assertNotIn("is_superuser", names)

    def test_create_school_class_and_validation(self):
        root = User.objects.create_superuser("root_sys2", password=PW)
        c = self.api(root)
        region = self.reg.pk
        r = c.post("/api/system/schools/new/", {"name": "مدرسه جدید", "parent": region, "code": "X1"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        bad = c.post("/api/system/schools/new/", {"name": "x", "parent": self.school.pk}, format="json")   # school under school
        self.assertEqual(bad.status_code, 400)
        ac = self.year.pk
        r = c.post("/api/system/classes/new/", {"school": self.school.pk, "academic_year": ac, "name": "نهم ج", "grade": self.grade.pk}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        dup = c.post("/api/system/classes/new/", {"school": self.school.pk, "academic_year": ac, "name": "نهم ج", "grade": self.grade.pk}, format="json")
        self.assertEqual(dup.status_code, 400)

    def test_users_cannot_get_passwords_and_protected_delete_is_409(self):
        root = User.objects.create_superuser("root_sys3", password=PW)
        c = self.api(root)
        r = c.post("/api/system/users/new/", {"username": "900000001", "first_name": "a", "last_name": "b", "role": "student",
                                              "org": self.school.pk, "password": "hack"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(User.objects.get(username="900000001").has_usable_password())
        d = c.delete(f"/api/system/classes/{self.classroom.pk}/")
        self.assertIn(d.status_code, (204, 409))                  # students/courses may protect it: never a 500
        self.assertNotEqual(c.delete(f"/api/system/users/{root.pk}/").status_code, 204)   # cannot delete yourself

    def test_subject_links_rule_on_save(self):
        root = User.objects.create_superuser("root_sys4", password=PW)
        prim = Level.objects.create(code="PRI2", name="ابتدایی2", order=1)
        pg = Grade.objects.create(code="P1", name="اول2", level=prim)
        from apps.academics.models import StudyField
        f = StudyField.objects.create(code="M9", name="ریاضی9", level=Level.objects.create(code="MS9", name="دوم9", order=9))
        r = self.api(root).post("/api/system/subjects/new/", {"code": "S900", "name": "درس", "grades": [pg.pk], "study_fields": [f.pk]}, format="json")
        self.assertEqual(r.status_code, 400)


class SystemPanelAllModulesTests(Base):
    def test_every_resource_lists_for_superuser(self):
        root = User.objects.create_superuser("root_all", password=PW)
        from apps.sysadmin.resources import RESOURCES
        c = self.api(root)
        for key in RESOURCES:
            r = c.get(f"/api/system/{key}/")
            self.assertEqual(r.status_code, 200, (key, r.status_code))

    def test_readonly_resources_refuse_writes(self):
        root = User.objects.create_superuser("root_ro", password=PW)
        c = self.api(root)
        self.assertEqual(c.post("/api/system/exams/new/", {"title": "x"}, format="json").status_code, 403)
        self.assertEqual(c.delete("/api/system/audit_log/1/").status_code, 403)

    def test_stats_and_non_super_denied(self):
        root = User.objects.create_superuser("root_st", password=PW)
        self.assertEqual(self.api(root).get("/api/system/stats/").status_code, 200)
        self.assertEqual(self.api(self.sa).get("/api/system/stats/").status_code, 403)


class SystemToolsTests(Base):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root_tools", password=PW)

    def test_tools_are_super_admin_only(self):
        for path in ("/api/system/tools/overview/",):
            self.assertEqual(self.api(self.sa).get(path).status_code, 403)
        self.assertEqual(self.api(self.sa).post("/api/system/tools/unlock/", {"username": "s1"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.root).get("/api/system/tools/overview/").status_code, 200)

    def test_mfa_enroll_shows_secret_once_and_reset(self):
        from apps.accounts.models import TOTPDevice
        r = self.api(self.root).post("/api/system/tools/mfa/", {"username": "s1", "action": "enroll"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["secret"])
        self.assertTrue(TOTPDevice.objects.get(user=self.s1).confirmed)
        self.assertNotIn(r.data["secret"], TOTPDevice.objects.get(user=self.s1).secret_enc)     # stored encrypted
        self.api(self.root).post("/api/system/tools/mfa/", {"username": "s1", "action": "reset"}, format="json")
        self.assertFalse(TOTPDevice.objects.filter(user=self.s1).exists())

    def test_current_year_exclusive_and_seed(self):
        from apps.academics.models import AcademicYear
        other = AcademicYear.objects.create(name="1499-1500", starts_on="2020-09-23", ends_on="2021-06-21")
        r = self.api(self.root).post("/api/system/tools/current-year/", {"id": other.pk}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AcademicYear.objects.filter(is_current=True).count(), 1)
        s = self.api(self.root).post("/api/system/tools/seed/", {"name": "catalog"}, format="json")
        self.assertEqual(s.status_code, 200)
        self.assertEqual(self.api(self.root).post("/api/system/tools/seed/", {"name": "rm -rf"}, format="json").status_code, 400)

    def test_unlock_and_sms_secret_never_returned(self):
        from apps.sms.models import SmsGateway
        gw = SmsGateway.objects.create(school=self.school, name="p", base_url="https://sms.example.ir/x")
        r = self.api(self.root).post("/api/system/tools/sms-secret/", {"gateway": gw.pk, "secret": "TOPSECRET"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("TOPSECRET", str(r.data))
        gw.refresh_from_db()
        self.assertTrue(gw.auth_secret_enc and "TOPSECRET" not in gw.auth_secret_enc)
        self.assertEqual(self.api(self.root).post("/api/system/tools/unlock/", {"username": "s1"}, format="json").status_code, 200)
