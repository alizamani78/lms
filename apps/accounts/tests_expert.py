from django.contrib.auth import get_user_model  # noqa: F401

from apps.academics.models import Level
from apps.accounts.models import OrgUnit, SchoolProfile, User
from apps.core.tests_phase2 import Base, PW


class ExpertTests(Base):
    def setUp(self):
        super().setUp()
        self.ms2 = Level.objects.create(code="MS2", name="متوسطه دوم", order=3)
        self.ms1 = Level.objects.create(code="MS1", name="متوسطه اول", order=2)
        SchoolProfile.objects.create(school=self.school, school_code="A1", level=self.ms2)
        SchoolProfile.objects.create(school=self.school2, school_code="B1", level=self.ms1)
        self.assess = User.objects.create_user("exp_assess", password=PW, role="expert", org=self.reg,
                                               expert_kind="assessment")
        self.curr = User.objects.create_user("exp_curr", password=PW, role="expert", org=self.reg,
                                             expert_kind="curriculum", expert_level=self.ms2)

    def test_assessment_expert_sees_region_schools_read_only(self):
        names = [s["name"] for s in self.api(self.assess).get("/api/me/schools/").data]
        self.assertEqual(sorted(names), sorted([self.school.name, self.school2.name]))
        self.assertEqual(self.api(self.assess).post("/api/courses/", {"title": "x"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.assess).get(f"/api/schools/{self.school.pk}/profile/").status_code, 200)
        self.assertEqual(self.api(self.assess).put(f"/api/schools/{self.school.pk}/profile/", {}, format="json").status_code, 403)
        self.assertEqual(self.api(self.assess).get("/api/system/schema/").status_code, 403)

    def test_curriculum_expert_only_sees_schools_of_its_level(self):
        names = [s["name"] for s in self.api(self.curr).get("/api/me/schools/").data]
        self.assertEqual(names, [self.school.name])
        self.assertEqual(self.api(self.curr).get(f"/api/schools/{self.school2.pk}/profile/").status_code, 404)

    def test_validation_of_expert_fields(self):
        bad = User(username="e1", role="expert", org=self.school)            # no kind
        with self.assertRaises(Exception):
            bad.full_clean()
        no_level = User(username="e2", role="expert", org=self.reg, expert_kind="curriculum")
        with self.assertRaises(Exception):
            no_level.full_clean()
        student = User(username="s9", role="student", org=self.school, expert_kind="assessment")
        student.full_clean()
        self.assertEqual(student.expert_kind, "")
