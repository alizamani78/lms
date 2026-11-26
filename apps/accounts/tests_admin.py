from django.contrib import admin
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import County, OrgUnit, Province, Region, School, User
from apps.courses.models import ContentItem, Course, Enrollment
from apps.integrations.health import validate_target

PW = "Str0ng-pass-1"


def req(user):
    r = RequestFactory().get("/")
    r.user = user
    return r


class AdminScopeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.p1 = OrgUnit.objects.create(name="استان۱", kind="province")
        self.p2 = OrgUnit.objects.create(name="استان۲", kind="province")
        self.c1 = OrgUnit.objects.create(name="شهرستان۱", kind="county", parent=self.p1)
        self.r1 = OrgUnit.objects.create(name="منطقه۱", kind="region", parent=self.c1)
        self.r2 = OrgUnit.objects.create(name="منطقه۲", kind="region", parent=self.p2)
        self.s1 = OrgUnit.objects.create(name="مدرسه۱", kind="school", parent=self.r1)
        self.s2 = OrgUnit.objects.create(name="مدرسه۲", kind="school", parent=self.r2)
        mk = lambda n, role, org: User.objects.create_user(n, password=PW, role=role, org=org)
        self.pa = mk("pa", "province_admin", self.p1)
        self.sa = mk("sa", "school_admin", self.s1)
        self.t1 = mk("t1", "teacher", self.s1)
        self.t2 = mk("t2", "teacher", self.s1)
        self.st = mk("st", "student", self.s1)
        self.c_a = Course.objects.create(org=self.s1, title="الف", teacher=self.t1)
        self.c_b = Course.objects.create(org=self.s1, title="ب", teacher=self.t2)
        self.ci_a = ContentItem.objects.create(course=self.c_a, title="a", kind="text", body="x")
        self.ci_b = ContentItem.objects.create(course=self.c_b, title="b", kind="text", body="y")

    def test_staff_flag_follows_role_and_superuser_gets_role(self):
        self.assertTrue(self.pa.is_staff and self.t1.is_staff)
        self.assertFalse(self.st.is_staff)
        root = User.objects.create_superuser("root", password=PW)
        self.assertEqual((root.role, root.is_staff), ("super_admin", True))

    def test_org_hierarchy_rules_and_subtree_move(self):
        with self.assertRaises(ValidationError):
            OrgUnit.objects.create(name="x", kind="school", parent=self.p1)       # school directly under province
        c2 = OrgUnit.objects.create(name="شهرستان۲", kind="county", parent=self.p1)
        self.r1.parent = c2
        self.r1.save()
        self.s1.refresh_from_db()
        self.assertTrue(self.s1.path.startswith(c2.path))                          # school moved with its region

    def test_province_admin_sees_only_own_subtree(self):
        ma = admin.site._registry[School]
        names = sorted(ma.get_queryset(req(self.pa)).values_list("name", flat=True))
        self.assertEqual(names, ["مدرسه۱"])
        with self.assertRaises(PermissionDenied):                  # cannot attach a school to another province
            ma.save_model(req(self.pa), School(name="x", parent=self.r2), None, False)
        self.assertTrue(admin.site._registry[School].has_add_permission(req(self.pa)))
        self.assertFalse(admin.site._registry[Province].has_add_permission(req(self.pa)))

    def test_school_admin_cannot_escalate(self):
        ma = admin.site._registry[User]
        field = User._meta.get_field("role")
        roles = {c[0] for c in ma.formfield_for_choice_field(field, req(self.sa)).choices}
        self.assertEqual(roles, {"teacher", "student"})
        with self.assertRaises(PermissionDenied):
            ma.save_model(req(self.sa), User(username="evil", role="province_admin", org=self.s1), None, False)
        with self.assertRaises(PermissionDenied):
            ma.save_model(req(self.sa), User(username="evil2", role="student", org=self.s2), None, False)  # other school
        self.assertNotIn("sa", ma.get_queryset(req(self.sa)).values_list("username", flat=True))

    def test_teacher_sees_only_own_content(self):
        ma = admin.site._registry[ContentItem]
        self.assertEqual(list(ma.get_queryset(req(self.t1)).values_list("title", flat=True)), ["a"])
        self.assertFalse(ma.has_change_permission(req(self.t1), self.ci_b))
        with self.assertRaises(PermissionDenied):
            ma.save_model(req(self.t1), ContentItem(course=self.c_b, title="z", kind="text", body="q"), None, False)
        self.assertFalse(admin.site._registry[User].has_module_permission(req(self.t1)))

    def test_content_needs_text_or_file_and_rich_text_is_sanitised(self):
        with self.assertRaises(ValidationError):
            ContentItem(course=self.c_a, title="x", kind="text", body="").full_clean()
        with self.assertRaises(ValidationError):
            ContentItem(course=self.c_a, title="x", kind="video").full_clean()
        item = ContentItem(course=self.c_a, title="x", kind="text",
                           body='<p onclick="evil()">سلام <b>ضخیم</b><script>alert(1)</script><img src="http://x/y.png"></p>')
        item.full_clean()
        item.save()
        self.assertNotIn("onclick", item.body)
        self.assertNotIn("<script", item.body)
        self.assertNotIn("http://x", item.body)
        self.assertIn("<b>ضخیم</b>", item.body)

    def test_ssrf_guard(self):
        for url in ("https://127.0.0.1/", "https://169.254.169.254/latest/meta-data", "ftp://example.com/"):
            with self.assertRaises(ValueError):
                validate_target(url)

    def test_all_admin_pages_render_for_superuser(self):
        root = User.objects.create_superuser("root2", password=PW)
        self.client.force_login(root)
        for model, ma in admin.site._registry.items():
            base = f"admin:{model._meta.app_label}_{model._meta.model_name}"
            r = self.client.get(reverse(base + "_changelist"))
            self.assertEqual(r.status_code, 200, base)
            if ma.has_add_permission(req(root)):
                self.assertEqual(self.client.get(reverse(base + "_add")).status_code, 200, base)


class ContentApiTests(TestCase):
    def setUp(self):
        cache.clear()
        prov = OrgUnit.objects.create(name="P", kind="province")
        reg = OrgUnit.objects.create(name="R", kind="region", parent=prov)
        self.school = OrgUnit.objects.create(name="A", kind="school", parent=reg)
        self.t = User.objects.create_user("t", password=PW, role="teacher", org=self.school)
        self.s1 = User.objects.create_user("s1", password=PW, role="student", org=self.school)
        self.s2 = User.objects.create_user("s2", password=PW, role="student", org=self.school)
        self.course = Course.objects.create(org=self.school, title="ریاضی", teacher=self.t)
        Enrollment.objects.create(course=self.course, student=self.s1)
        ContentItem.objects.create(course=self.course, title="منتشر", kind="text", body="x", is_published=True)
        ContentItem.objects.create(course=self.course, title="پیش‌نویس", kind="text", body="y")

    def get(self, user):
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": user.username, "password": PW}, format="json")
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return c.get(f"/api/courses/{self.course.pk}/content/")

    def test_student_sees_published_only(self):
        r = self.get(self.s1)
        self.assertEqual([i["title"] for i in r.data], ["منتشر"])

    def test_teacher_sees_all_and_unenrolled_student_blocked(self):
        self.assertEqual(len(self.get(self.t).data), 2)
        self.assertEqual(self.get(self.s2).status_code, 404)


class LoginAuditRegressionTests(TestCase):
    """Regression: the login endpoint must answer (it once crashed because of a missing import)."""

    def test_login_endpoint_answers(self):
        from django.test import Client
        from apps.accounts.models import User
        User.objects.create_user("regress_login", password="Str0ng-pass-1", role="student")
        r = Client().post("/api/auth/login/", {"username": "regress_login", "password": "Str0ng-pass-1"}, content_type="application/json")
        self.assertIn(r.status_code, (200, 401, 400, 423, 428))
        self.assertNotEqual(r.status_code, 500)


class OneCountyAndLoginTests(TestCase):
    def setUp(self):
        from apps.accounts.models import OrgUnit
        self.prov = OrgUnit.objects.create(name="استان خ", kind="province")
        self.c1 = OrgUnit.objects.create(name="شهرستان ۱", kind="county", parent=self.prov)
        self.c2 = OrgUnit.objects.create(name="شهرستان ۲", kind="county", parent=self.prov)
        self.r1 = OrgUnit.objects.create(name="ناحیه ۱", kind="region", parent=self.c1)
        self.r2 = OrgUnit.objects.create(name="ناحیه ۲", kind="region", parent=self.c2)
        self.s1 = OrgUnit.objects.create(name="مدرسه الف", kind="school", parent=self.r1)
        self.s2 = OrgUnit.objects.create(name="مدرسه ب", kind="school", parent=self.r1)
        self.s3 = OrgUnit.objects.create(name="مدرسه ج", kind="school", parent=self.r2)

    def test_teacher_in_two_counties_is_refused_and_two_schools_in_one_county_allowed(self):
        from django.core.exceptions import ValidationError
        from apps.accounts.models import SchoolMembership, User
        t = User.objects.create_user("t_county", password="Str0ng-pass-1", role="teacher", org=self.s1)
        SchoolMembership.objects.get_or_create(user=t, school=self.s1)
        SchoolMembership(user=t, school=self.s2).full_clean()                    # same county: fine
        with self.assertRaises(ValidationError):
            SchoolMembership(user=t, school=self.s3).full_clean()                 # another county: refused

    def test_login_by_national_id_or_username(self):
        from django.test import Client
        from apps.accounts.models import User
        u = User.objects.create_user("login_nid", password="Str0ng-pass-1", role="student", national_id="0499370899")
        c = Client()
        for who in ("login_nid", "0499370899"):
            r = c.post("/api/auth/login/", {"username": who, "password": "Str0ng-pass-1"}, content_type="application/json")
            self.assertEqual(r.status_code, 200, (who, r.status_code))


class UsernameOptionalTests(TestCase):
    """People sign in with the national code and a password; only administrators have a typed username."""

    def test_username_made_from_national_code_when_empty(self):
        from apps.accounts.models import User
        u = User.objects.create_user("", password="Str0ng-pass-1", role="teacher", national_id="0499370899")
        self.assertEqual(u.username, "0499370899")
        from django.test import Client
        r = Client().post("/api/auth/login/", {"username": "0499370899", "password": "Str0ng-pass-1"}, content_type="application/json")
        self.assertEqual(r.status_code, 200)

    def test_two_users_same_code_get_distinct_usernames(self):
        from apps.accounts.models import User
        a = User.objects.create_user("", password="Str0ng-pass-1", role="student", student_code="960000777")
        b = User.objects.create_user("", password="Str0ng-pass-1", role="student", student_code="960000777")
        self.assertNotEqual(a.username, b.username)


class AdditionalRolesTests(TestCase):
    """A person can be a teacher in one school and a deputy in another, or a principal who also teaches in his school."""

    def setUp(self):
        from apps.accounts.models import OrgUnit
        self.county = OrgUnit.objects.create(name="شهرستان هفت", kind="county")
        self.region = OrgUnit.objects.create(name="ناحیه هفت", kind="region", parent=self.county)
        self.s1 = OrgUnit.objects.create(name="مدرسه هفت الف", kind="school", parent=self.region)
        self.s2 = OrgUnit.objects.create(name="مدرسه هفت ب", kind="school", parent=self.region)
        self.person = User.objects.create_user("multi_role", password="Str0ng-pass-1", role="teacher", org=self.s1)

    def test_deputy_in_another_school_is_in_scope_and_passes_the_role_check(self):
        from apps.accounts.models import RoleAssignment
        from apps.core.permissions import role_required
        from apps.core.scope import org_in_scope
        RoleAssignment.objects.create(user=self.person, role="school_deputy_edu", school=self.s2)
        self.assertTrue(org_in_scope(self.person, self.s2))
        self.assertTrue(org_in_scope(self.person, self.s1))
        class _Req:
            user = self.person
        self.assertTrue(role_required("school_deputy_edu")().has_permission(_Req(), None))

    def test_school_role_needs_its_school(self):
        from django.core.exceptions import ValidationError
        from apps.accounts.models import RoleAssignment
        with self.assertRaises(ValidationError):
            RoleAssignment(user=self.person, role="school_deputy_exec", school=None).full_clean()

    def test_teacher_role_in_school_creates_membership(self):
        from apps.accounts.models import RoleAssignment, SchoolMembership
        RoleAssignment.objects.create(user=self.person, role="teacher", school=self.s2)
        self.assertTrue(SchoolMembership.objects.filter(user=self.person, school=self.s2).exists())
