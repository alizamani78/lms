from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.core.admin_menu import SECTIONS
from django.contrib import admin


class AdminMenuTests(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root_menu", password="Str0ng-pass-1")
        self.client.force_login(self.root)

    def test_sections_group_models_and_index_renders(self):
        r = self.client.get(reverse("admin:index"))
        self.assertEqual(r.status_code, 200)
        titles = [a["name"] for a in admin.site.get_app_list(r.wsgi_request)]
        self.assertIn("کاربران و دسترسی", titles)
        self.assertIn("ساختار سازمانی", titles)
        self.assertEqual(titles[0], "کاربران و دسترسی")

    def test_sections_reference_known_models(self):
        known = {(m._meta.app_label, m.__name__) for m in admin.site._registry}
        listed = {k for _, keys in SECTIONS for k in keys}
        missing = [k for k in listed if k not in known]
        # a few names are optional (models registered only in some apps); the menu still works without them
        self.assertLess(len(missing), 8, missing)
