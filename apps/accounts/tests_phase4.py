import io
import zipfile

from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.academics.models import Level
from apps.accounts.models import OrgUnit, SchoolMembership, SchoolProfile, gender_allowed
from apps.core.scope import scope_paths
from apps.core.tests_phase2 import Base

import tempfile
TMP = tempfile.mkdtemp()


def png_bytes():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (800, 600), (30, 120, 90)).save(b, "PNG")
    return b.getvalue()


def _upload(name, data):
    return SimpleUploadedFile(name, data)


class ScopeTests(Base):
    def test_region_admin_sees_all_schools_below_by_default(self):
        self.assertEqual(scope_paths(self.ra), [self.reg.path])
        self.assertEqual(len(self.api(self.ra).get("/api/me/schools/").data), 2)

    def test_principal_only_sees_memberships(self):
        other = OrgUnit.objects.create(name="C", kind="school", parent=self.reg)
        self.assertEqual([s["id"] for s in self.api(self.sa).get("/api/me/schools/").data], [self.school.pk])
        SchoolMembership.objects.create(user=self.sa, school=other)
        self.assertEqual({s["id"] for s in self.api(self.sa).get("/api/me/schools/").data}, {self.school.pk, other.pk})

    def test_kartabl_filters_lists(self):
        from rest_framework.test import APIClient
        k = APIClient()
        k.force_authenticate(self.ra)
        k.credentials(HTTP_X_SCHOOL_ID=str(self.school2.pk))
        self.assertEqual(k.get("/api/courses/").data["count"], 0)
        k.credentials(HTTP_X_SCHOOL_ID=str(self.school.pk))
        self.assertEqual(k.get("/api/courses/").data["count"], 1)
        k.credentials(HTTP_X_SCHOOL_ID="99999")               # not in scope -> ignored
        self.assertEqual(k.get("/api/courses/").data["count"], 1)


class ProfileAndGenderTests(Base):
    def setUp(self):
        super().setUp()
        self.ms2 = Level.objects.create(code="MS2", name="متوسطه دوره دوم", order=3)
        self.girls = OrgUnit.objects.create(name="دخترانه", kind="school", parent=self.reg)
        self.boys = OrgUnit.objects.create(name="پسرانه", kind="school", parent=self.reg)
        SchoolProfile.objects.create(school=self.girls, school_code="1001", level=self.ms2, gender="girls")
        SchoolProfile.objects.create(school=self.boys, school_code="1002", level=self.ms2, gender="boys")

    def test_gender_rule(self):
        self.assertFalse(gender_allowed("boy", SchoolProfile.objects.get(school=self.girls)))
        self.assertTrue(gender_allowed("girl", SchoolProfile.objects.get(school=self.girls)))
        self.assertFalse(gender_allowed("girl", SchoolProfile.objects.get(school=self.boys)))

    def test_school_code_unique_and_profile_put(self):
        r = self.api(self.ra).put(f"/api/schools/{self.boys.pk}/profile/", {"school_code": "1001", "level": self.ms2.pk}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.api(self.ra).put(f"/api/schools/{self.boys.pk}/profile/",
                                  {"school_code": "1002", "level": self.ms2.pk, "address": "مشهد"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.api(self.t1).put(f"/api/schools/{self.school.pk}/profile/", {}, format="json").status_code, 403)

    def test_geo_cascade(self):
        prov = OrgUnit.objects.create(name="خراسان رضوی", kind="province")
        reg2 = OrgUnit.objects.create(name="ناحیه ۳", kind="region", parent=prov)
        ids = [o["id"] for o in self.api(self.ra).get(f"/api/geo/?parent={prov.pk}").data]
        self.assertEqual(ids, [reg2.pk])


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class PhotoTests(Base):
    def test_upload_processed_and_access_checked(self):
        t = self.api(self.s1).put(f"/api/users/{self.s1.pk}/photo/", {"file": _upload("me.png", png_bytes())}, format="multipart")
        self.assertEqual(t.status_code, 200)
        self.assertEqual(self.api(self.s1).get(f"/api/users/{self.s1.pk}/photo/").status_code, 200)
        self.assertEqual(self.api(self.t1).get(f"/api/users/{self.s1.pk}/photo/").status_code, 200)
        self.assertEqual(self.api(self.s2).get(f"/api/users/{self.s1.pk}/photo/").status_code, 404)
        self.assertEqual(self.api(self.t2).get(f"/api/users/{self.s1.pk}/photo/").status_code, 404)
        bad = self.api(self.s1).put(f"/api/users/{self.s1.pk}/photo/", {"file": _upload("x.png", b"notimage")}, format="multipart")
        self.assertEqual(bad.status_code, 400)

    def test_zip_matches_by_code_and_reports(self):
        self.s1.student_code = "970000001"
        self.s1.save()
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("970000001.png", png_bytes())
            z.writestr("unknown.png", png_bytes())
            z.writestr("bad.png", b"junk")
        r = self.api(self.ra).post(f"/api/photos/zip/?unit={self.school.pk}", {"file": _upload("p.zip", b.getvalue())}, format="multipart")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["photos_saved"], 1)
        self.assertIn("unknown.png", r.data["unmatched"])
        self.assertIn("bad.png", r.data["invalid"])
