from django.test import TestCase

from apps.accounts.models import OrgUnit, SchoolMembership, User
from apps.core.scope import scope_paths


class EducationalDeputyRoleTests(TestCase):
    def setUp(self):
        self.county = OrgUnit.objects.create(name="شهرستان دو", kind="county")
        self.region = OrgUnit.objects.create(name="ناحیه دو", kind="region", parent=self.county)
        self.school = OrgUnit.objects.create(name="مدرسه دو", kind="school", parent=self.region)
        self.other_county = OrgUnit.objects.create(name="شهرستان سه", kind="county")
        self.other_school = OrgUnit.objects.create(name="مدرسه سه", kind="school", parent=self.other_county)
        self.deputy = User.objects.create_user("deputy_edu1", password="Str0ng-pass-1", role="school_deputy_edu", org=self.school)
        SchoolMembership.objects.create(user=self.deputy, school=self.school)

    def test_scope_is_the_school_and_not_admin(self):
        self.assertEqual(scope_paths(self.deputy), [self.school.path])
        self.assertNotIn("school_deputy_edu", User.ADMIN_ROLES)
        self.assertIn("school_deputy_edu", User.STAFF_ROLES)
        self.assertLess(User.RANK["school_admin"], User.RANK["school_deputy_edu"])

    def test_deputy_works_in_one_county_only(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            SchoolMembership(user=self.deputy, school=self.other_school).full_clean()


class ExecutiveDeputyTests(TestCase):
    def setUp(self):
        self.county = OrgUnit.objects.create(name="شهرستان چهار", kind="county")
        self.region = OrgUnit.objects.create(name="ناحیه چهار", kind="region", parent=self.county)
        self.school = OrgUnit.objects.create(name="مدرسه چهار", kind="school", parent=self.region)
        self.other = OrgUnit.objects.create(name="شهرستان پنج", kind="county")
        self.other_school = OrgUnit.objects.create(name="مدرسه پنج", kind="school", parent=self.other)
        self.exec = User.objects.create_user("deputy_exec1", password="Str0ng-pass-1", role="school_deputy_exec", org=self.school)
        SchoolMembership.objects.create(user=self.exec, school=self.school)

    def test_scope_staff_and_reports(self):
        from apps.reports.catalog import SCHOOL_ROLES
        self.assertEqual(scope_paths(self.exec), [self.school.path])
        self.assertIn("school_deputy_exec", User.STAFF_ROLES)          # may see and record grades like staff do
        self.assertNotIn("school_deputy_exec", User.ADMIN_ROLES)
        self.assertIn("school_deputy_exec", SCHOOL_ROLES)              # grade reports of his school

    def test_one_county_rule_applies(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            SchoolMembership(user=self.exec, school=self.other_school).full_clean()
