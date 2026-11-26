from django.core.cache import cache
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import OrgUnit, User
from apps.courses.models import Course, Enrollment

PW = "Str0ng-pass-1"


class ProfileTests(APITestCase):
    def setUp(self):
        cache.clear()
        prov = OrgUnit.objects.create(name="خراسان رضوی", kind="province")
        self.reg = OrgUnit.objects.create(name="منطقه ۱", kind="region", parent=prov)
        self.reg2 = OrgUnit.objects.create(name="منطقه ۲", kind="region", parent=prov)
        self.a = OrgUnit.objects.create(name="مدرسه الف", kind="school", parent=self.reg)
        self.b = OrgUnit.objects.create(name="مدرسه ب", kind="school", parent=self.reg)
        self.c = OrgUnit.objects.create(name="مدرسه ج", kind="school", parent=self.reg2)
        self.t = User.objects.create_user("t1", password=PW, role="teacher", org=self.a)
        self.s = User.objects.create_user("s1", password=PW, role="student", org=self.a, first_name="علی")
        self.ra = User.objects.create_user("ra", password=PW, role="region_admin", org=self.reg)
        self.course = Course.objects.create(org=self.a, title="ریاضی", teacher=self.t)
        Enrollment.objects.create(course=self.course, student=self.s)

    def client_for(self, user, pw=PW):
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": user.username, "password": pw}, format="json")
        self.assertEqual(r.status_code, 200)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return c

    def test_me_has_org_chain(self):
        d = self.client_for(self.s).get("/api/me/").data
        self.assertEqual([o["name"] for o in d["org_chain"]], ["خراسان رضوی", "منطقه ۱", "مدرسه الف"])
        self.assertEqual(d["role"], "student")

    def test_cannot_change_role_or_org_via_profile(self):
        c = self.client_for(self.s)
        r = c.patch("/api/me/", {"first_name": "رضا", "role": "super_admin", "org": self.c.pk, "username": "x"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.s.refresh_from_db()
        self.assertEqual((self.s.first_name, self.s.role, self.s.org_id, self.s.username), ("رضا", "student", self.a.pk, "s1"))

    def test_password_change(self):
        c = self.client_for(self.s)
        self.assertEqual(c.post("/api/me/password/", {"old_password": "wrong", "new_password": "N3w-strong-pass"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/me/password/", {"old_password": PW, "new_password": "123"}, format="json").status_code, 400)
        self.assertEqual(c.post("/api/me/password/", {"old_password": PW, "new_password": "N3w-strong-pass"}, format="json").status_code, 204)
        self.client_for(self.s, "N3w-strong-pass")

    def test_schools_by_role(self):
        names = lambda u: sorted(s["name"] for s in self.client_for(u).get("/api/schools/").data["results"])
        self.assertEqual(names(self.s), ["مدرسه الف"])
        self.assertEqual(names(self.t), ["مدرسه الف"])
        self.assertEqual(names(self.ra), ["مدرسه الف", "مدرسه ب"])     # not مدرسه ج (other region)

    def test_courses_show_names_and_school_filter(self):
        c = self.client_for(self.s)
        row = c.get("/api/courses/").data["results"][0]
        self.assertEqual((row["org_name"], row["title"]), ("مدرسه الف", "ریاضی"))
        self.assertEqual(c.get(f"/api/courses/?org={self.b.pk}").data["count"], 0)

    def test_requires_login(self):
        self.assertEqual(APIClient().get("/api/me/").status_code, 401)
        self.assertEqual(APIClient().get("/api/schools/").status_code, 401)

    def test_app_page_has_csp_nonce(self):
        r = APIClient().get("/")
        self.assertEqual(r.status_code, 200)
        csp = r["Content-Security-Policy"]
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'nonce="{nonce}"', r.content.decode())
        self.assertNotIn("unsafe-inline", csp)
