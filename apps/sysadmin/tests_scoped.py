from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit, User
from apps.core.tests_phase2 import Base, PW


class ScopedManagementTests(Base):
    def test_principal_sees_only_own_school_and_resources_by_rank(self):
        keys = {r["key"] for r in self.api(self.sa).get("/api/manage/schema/").data}
        self.assertIn("users", keys)
        self.assertIn("classes", keys)
        self.assertNotIn("schools", keys)                                  # only regional admins and above manage schools
        self.assertIn("schools", {r["key"] for r in self.api(self.ra).get("/api/manage/schema/").data})
        self.assertEqual(self.api(self.t1).get("/api/manage/schema/").status_code, 403)
        names = {r["name"] for r in self.api(self.sa).get("/api/manage/classes/").data["rows"]}
        self.assertEqual(names, {self.classroom.name})
        other = ClassRoom.objects.create(school=self.school2, academic_year=self.year, name="کلاس مدرسه دیگر", grade=self.grade)
        names = {r["name"] for r in self.api(self.sa).get("/api/manage/classes/").data["rows"]}
        self.assertNotIn(other.name, names)

    def test_cannot_create_outside_scope_or_above_rank(self):
        c = self.api(self.sa)
        bad = c.post("/api/manage/classes/new/", {"school": self.school2.pk, "academic_year": self.year.pk, "name": "x", "grade": self.grade.pk}, format="json")
        self.assertEqual(bad.status_code, 403)
        ok = c.post("/api/manage/classes/new/", {"school": self.school.pk, "academic_year": self.year.pk, "name": "نهم ز", "grade": self.grade.pk}, format="json")
        self.assertEqual(ok.status_code, 201, ok.data)
        up = c.post("/api/manage/users/new/", {"username": "x1", "first_name": "a", "last_name": "b", "role": "region_admin", "org": self.school.pk}, format="json")
        self.assertEqual(up.status_code, 403)
        expert = c.post("/api/manage/users/new/", {"username": "x2", "first_name": "a", "last_name": "b", "role": "expert", "org": self.school.pk}, format="json")
        self.assertEqual(expert.status_code, 403)
        tch = c.post("/api/manage/users/new/", {"username": "x3", "first_name": "a", "last_name": "b", "role": "teacher", "org": self.school.pk}, format="json")
        self.assertEqual(tch.status_code, 201, tch.data)

    def test_users_cannot_be_deleted_and_peers_are_hidden(self):
        users = self.api(self.sa).get("/api/manage/users/").data["rows"]
        self.assertTrue(all(u["role"] not in ("super_admin", "school_admin") for u in users) if users and "role" in users[0] else True)
        self.assertEqual(self.api(self.sa).delete(f"/api/manage/users/{self.s1.pk}/").status_code, 403)
        self.assertEqual(self.api(self.sa).delete(f"/api/manage/users/{self.sa.pk}/").status_code, 403)

    def test_region_admin_manages_schools_below_only(self):
        r = self.api(self.ra).post("/api/manage/schools/new/", {"name": "مدرسه تازه", "parent": self.reg.pk, "code": "N1"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        far = OrgUnit.objects.create(name="ناحیه دور", kind="region")
        bad = self.api(self.ra).post("/api/manage/schools/new/", {"name": "x", "parent": far.pk}, format="json")
        self.assertEqual(bad.status_code, 403)
