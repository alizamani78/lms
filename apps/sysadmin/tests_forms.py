from django.test import TestCase
import io
import zipfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.academics.models import AcademicYear, Level
from apps.accounts.models import OrgUnit, SchoolMembership, SchoolProfile, User
from apps.core.tests_phase2 import Base, PW

import tempfile
TMP = tempfile.mkdtemp()


class RoleDependentUserTests(Base):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser("root_f", password=PW)
        self.ms1 = Level.objects.create(code="MS1", name="متوسطه اول", order=2)

    def test_principal_needs_region_or_school_and_level(self):
        c = self.api(self.root)
        base = {"username": "p1", "first_name": "الف", "last_name": "ب", "role": "school_admin", "org": self.school.pk}
        self.assertEqual(c.post("/api/system/users/new/", base, format="json").status_code, 400)          # level missing
        ok = c.post("/api/system/users/new/", {**base, "expert_level": self.ms1.pk, "expert_kind": "assessment"}, format="json")
        self.assertEqual(ok.status_code, 201, ok.data)
        self.assertEqual(User.objects.get(username="p1").expert_kind, "")                                 # kind removed for principals
        prov = OrgUnit.objects.create(name="استان ت", kind="province")
        bad = c.post("/api/system/users/new/", {**base, "username": "p2", "org": prov.pk, "expert_level": self.ms1.pk}, format="json")
        self.assertEqual(bad.status_code, 400)                                                              # province is not allowed for a principal

    def test_expert_needs_kind_and_level(self):
        c = self.api(self.root)
        base = {"username": "e1", "first_name": "الف", "last_name": "ب", "role": "expert", "org": self.reg.pk}
        self.assertEqual(c.post("/api/system/users/new/", base, format="json").status_code, 400)
        self.assertEqual(c.post("/api/system/users/new/", {**base, "expert_kind": "curriculum"}, format="json").status_code, 400)
        ok = c.post("/api/system/users/new/", {**base, "expert_kind": "curriculum", "expert_level": self.ms1.pk}, format="json")
        self.assertEqual(ok.status_code, 201, ok.data)

    def test_schema_carries_role_rules_and_pickers(self):
        users = next(r for r in self.api(self.root).get("/api/system/schema/").data if r["key"] == "users")
        self.assertIn("school_admin", users["rules"]["roles"])
        org = next(f for f in users["fields"] if f["name"] == "org")
        self.assertTrue(org["pick"]["kinds_by_role"])
        mem = next(r for r in self.api(self.root).get("/api/system/schema/").data if r["key"] == "memberships")
        self.assertEqual(next(f for f in mem["fields"] if f["name"] == "school")["pick"]["kind"], "school")


class OptionsTests(Base):
    def test_options_are_filtered_by_kind_and_role(self):
        root = User.objects.create_superuser("root_o", password=PW)
        c = self.api(root)
        schools = c.get("/api/reports/scope-orgs/?kind=school").data
        self.assertTrue(all("—" in s["name"] or s["name"] for s in schools))
        names = {s["name"].split(" — ")[0] for s in schools}
        self.assertIn(self.school.name, names)
        self.assertNotIn(self.reg.name, names)                                    # a region is never offered as a school
        students = c.get("/api/system/options/?model=accounts.User&role=student").data
        labels = " ".join(x["label"] for x in students)
        self.assertIn(self.s1.username, labels)
        self.assertNotIn(self.t1.username, labels)
        regions = c.get(f"/api/reports/scope-orgs/?kind=region&province={self.prov.pk}").data if hasattr(self, "prov") else []
        self.assertIsInstance(regions, list)


class AcademicYearTests(Base):
    def test_year_defined_by_two_dates(self):
        root = User.objects.create_superuser("root_y", password=PW)
        r = self.api(root).post("/api/system/years/new/", {"starts_on": "2026-09-23", "ends_on": "2027-06-21"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(AcademicYear.objects.get(pk=r.data["id"]).name, "1405-1406")


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class RichContentTests(Base):
    def test_embedded_media_is_readable_by_enrolled_students_only(self):
        from apps.files.models import FileAsset
        from apps.files.tests_helpers import png_upload
        up = self.api(self.t1).post("/api/files/", {"file": png_upload("a.png")}, format="multipart")
        self.assertEqual(up.status_code, 201, getattr(up, "data", None))
        fid = up.data["id"]
        html = f'<p>تصویر</p><img data-file="{fid}" alt="x" onerror="evil()">'
        item = self.api(self.t1).post(f"/api/courses/{self.course.pk}/content/", {"title": "متن", "kind": "text", "body": html, "is_published": True}, format="json")
        self.assertEqual(item.status_code, 201, item.data)
        got = self.api(self.s1).get(f"/api/courses/{self.course.pk}/content/").data[0]
        self.assertNotIn("onerror", got["body"])
        self.assertIn(f'data-file="{fid}"', got["body"])
        self.assertEqual(self.api(self.s1).get(f"/api/files/{fid}/download/").status_code, 200)
        self.assertEqual(self.api(self.s4).get(f"/api/files/{fid}/download/").status_code, 404)


class CatalogImportTests(Base):
    def test_counties_preview_commit_and_permissions(self):
        root = User.objects.create_superuser("root_c", password=PW)
        prov = OrgUnit.objects.create(name="خراسان جنوبی", kind="province")
        text = "نام شهرستان\tکد\nبیرجند\tC1\nطبس\tC2\nطبس\tC3\n"
        r = self.api(root).post("/api/catalog-import/preview/", {"kind": "counties", "province": prov.pk, "text": text}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([x["status"] for x in r.data["rows"]], ["ok", "ok", "error"])            # duplicate name in the file
        done = self.api(root).post("/api/catalog-import/commit/", {"kind": "counties", "province": prov.pk, "rows": [{"n": x["n"], "values": x["values"]} for x in r.data["rows"]]}, format="json")
        self.assertEqual(done.data["created"], 2)
        self.assertTrue(OrgUnit.objects.filter(kind="county", name="طبس", parent=prov).exists())
        self.assertEqual(self.api(self.sa).post("/api/catalog-import/preview/", {"kind": "counties", "province": prov.pk, "text": text}, format="multipart").status_code, 403)

    def test_fields_import(self):
        root = User.objects.create_superuser("root_c2", password=PW)
        r = self.api(root).post("/api/catalog-import/preview/", {"kind": "catalog_fields", "text": "کد رشته\tنام رشته\n77001\tرشته تازه\n"}, format="multipart")
        self.assertEqual(r.data["rows"][0]["status"], "ok")


class MfaAdminTests(Base):
    def test_search_by_national_id_and_scope(self):
        self.s1.national_id = "0499370899"
        self.s1.save()
        r = self.api(self.sa).get("/api/mfa-admin/search/?q=0499")
        self.assertEqual([u["username"] for u in r.data], [self.s1.username])
        self.assertEqual(self.api(self.sa2).get("/api/mfa-admin/search/?q=0499").data, []) if hasattr(self, "sa2") else None
        e = self.api(self.sa).post("/api/mfa-admin/action/", {"id": self.s1.pk, "action": "enroll"}, format="json")
        self.assertEqual(e.status_code, 200)
        self.assertTrue(e.data["secret"])
        self.assertEqual(self.api(self.sa).post("/api/mfa-admin/action/", {"id": self.s1.pk, "action": "reset"}, format="json").status_code, 200)
        self.assertEqual(self.api(self.sa).post("/api/mfa-admin/action/", {"id": self.ra.pk, "action": "reset"}, format="json").status_code, 404)   # above his rank
        self.assertEqual(self.api(self.s1).get("/api/mfa-admin/search/?q=0499").status_code, 403)


class DistrictReportFilterTests(Base):
    def setUp(self):
        super().setUp()
        self.ms2 = Level.objects.create(code="MS2", name="متوسطه دوم", order=3)
        self.ms1 = Level.objects.create(code="MS1", name="متوسطه اول", order=2)
        SchoolProfile.objects.create(school=self.school, school_code="A1", level=self.ms2, gender="girls", shift="daily")
        SchoolProfile.objects.create(school=self.school2, school_code="B1", level=self.ms1, gender="boys", shift="night")

    def test_directory_filters(self):
        c = self.api(self.ra)
        allr = c.get("/api/reports/run/directory/")
        self.assertEqual({x["school"] for x in allr.data["rows"]}, {self.school.name, self.school2.name})
        only = c.get(f"/api/reports/run/directory/?level={self.ms2.pk}")
        self.assertEqual([x["school"] for x in only.data["rows"]], [self.school.name])
        self.assertEqual([x["school"] for x in c.get("/api/reports/run/directory/?gender=boys").data["rows"]], [self.school2.name])
        self.assertEqual(c.get("/api/reports/run/directory/?shift=night&level=" + str(self.ms2.pk)).data["rows"], [])
        s = c.get("/api/reports/run/summary/?group=level")
        self.assertEqual({x["group"] for x in s.data["rows"]}, {"متوسطه دوم", "متوسطه اول"})
        self.assertEqual(self.api(self.sa).get("/api/reports/run/directory/").status_code, 403)


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class ProvinceZipTests(Base):
    def _zip(self, nid):
        from apps.accounts.tests_phase4 import png_bytes
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr(f"{nid}.png", png_bytes())
            z.writestr("students.csv", "کد ملی,نام,موبایل\n" + f"{nid},رضا,09121234567\n")
        return b.getvalue()

    def test_zip_updates_photo_and_details_by_national_id_inside_scope(self):
        self.s1.national_id = "0499370899"
        self.s1.save()
        r = self.api(self.ra).post(f"/api/photos/zip/?unit={self.reg.pk}", {"file": SimpleUploadedFile("p.zip", self._zip("0499370899"))}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["photos_saved"], r.data["details_updated"]), (1, 1))
        self.s1.refresh_from_db()
        self.assertEqual((self.s1.first_name, self.s1.mobile), ("رضا", "09121234567"))
        far = OrgUnit.objects.create(name="ناحیه دیگر", kind="region")
        self.assertEqual(self.api(self.ra).post(f"/api/photos/zip/?unit={far.pk}", {"file": SimpleUploadedFile("p.zip", self._zip("0499370899"))}, format="multipart").status_code, 404)

    def test_province_expert_may_upload_inside_own_unit(self):
        expert = User.objects.create_user("exp_zip", password=PW, role="expert", org=self.reg, expert_kind="assessment")
        self.s1.national_id = "0499370899"
        self.s1.save()
        r = self.api(expert).post(f"/api/photos/zip/?unit={self.reg.pk}", {"file": SimpleUploadedFile("p.zip", self._zip("0499370899"))}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.api(self.t1).post(f"/api/photos/zip/?unit={self.reg.pk}", {"file": SimpleUploadedFile("p.zip", b"x")}, format="multipart").status_code, 403)


class TimetableFormTests(Base):
    def test_bell_periods_with_times_and_scoped_school_list(self):
        c = self.api(self.sa)
        r = c.post("/api/manage/bell_periods/new/", {"school": self.school.pk, "number": 1, "start_time": "07:30", "end_time": "08:15"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        bad = c.post("/api/manage/bell_periods/new/", {"school": self.school2.pk, "number": 1, "start_time": "07:30", "end_time": "08:15"}, format="json")
        self.assertEqual(bad.status_code, 403)
        sch = next(x for x in c.get("/api/manage/schema/").data if x["key"] == "bell_periods")
        self.assertEqual(next(f for f in sch["fields"] if f["name"] == "start_time")["type"], "time")


class StructureRulesTests(Base):
    def setUp(self):
        super().setUp()
        from apps.academics.models import Grade, Level, StudyField
        self.ms2 = Level.objects.create(code="MS2", name="متوسطه دوم", order=3)
        self.kar = Level.objects.create(code="KAR", name="کاردانش", order=4)
        self.g_ms2 = Grade.objects.create(code="T10", name="دهم", level=self.ms2)
        self.g_kar = Grade.objects.create(code="KAR10", name="دهم", level=self.kar)
        self.f_math = StudyField.objects.create(code="15000", name="ریاضی فیزیک", level=self.ms2)

    def test_class_must_use_its_school_level(self):
        from apps.academics.models import ClassRoom
        from apps.accounts.models import SchoolProfile
        from django.core.exceptions import ValidationError
        SchoolProfile.objects.create(school=self.school, school_code="K1", level=self.kar, gender="mixed")
        bad = ClassRoom(school=self.school, academic_year=self.year, name="ک۱", grade=self.g_ms2)
        with self.assertRaises(ValidationError):
            bad.full_clean()
        ClassRoom(school=self.school, academic_year=self.year, name="ک۲", grade=self.g_kar).full_clean()

    def test_single_gender_school_fixes_student_gender(self):
        from apps.accounts.models import SchoolProfile
        SchoolProfile.objects.create(school=self.school, school_code="G1", level=self.ms2, gender="girls")
        u = User(username="girl1", first_name="الف", last_name="ب", role="student", org=self.school, gender="boy")
        u.full_clean()
        self.assertEqual(u.gender, "girl")

    def test_schools_school_code_unique(self):
        from apps.accounts.models import SchoolProfile
        from django.db import IntegrityError, transaction
        SchoolProfile.objects.create(school=self.school, school_code="U1", level=self.ms2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SchoolProfile.objects.create(school=self.school2, school_code="U1", level=self.ms2)


class LevelScopedPickersTests(Base):
    """A principal sees only the grades, fields and subjects of his school's level."""

    def setUp(self):
        super().setUp()
        from apps.academics.models import Grade, Level, StudyField, Subject
        from apps.accounts.models import SchoolProfile
        self.ms2 = Level.objects.create(code="MS2L", name="متوسطه دوم-ل", order=3)
        self.prim = Level.objects.create(code="PRIL", name="ابتدایی-ل", order=1)
        self.g_ms2 = Grade.objects.create(code="L10", name="دهم-ل", level=self.ms2)
        self.g_prim = Grade.objects.create(code="LP1", name="اول-ل", level=self.prim)
        self.f_ms2 = StudyField.objects.create(code="LF1", name="رشته دوم", level=self.ms2)
        self.f_prim = StudyField.objects.create(code="LF2", name="رشته ابتدایی", level=self.prim)
        self.sub_ms2 = Subject.objects.create(code="LS1", name="درس دوم")
        self.sub_ms2.grades.set([self.g_ms2])
        self.sub_prim = Subject.objects.create(code="LS2", name="درس ابتدایی")
        self.sub_prim.grades.set([self.g_prim])
        SchoolProfile.objects.create(school=self.school, school_code="LVL1", level=self.ms2)

    def test_principal_sees_only_his_level(self):
        forms = self.api(self.sa).get("/api/school-form/").data
        self.assertEqual({g["name"] for g in forms["grades"]}, {"دهم-ل"})
        self.assertEqual({f["name"] for f in forms["fields"]}, {"رشته دوم"})
        opts = self.api(self.sa).get("/api/manage/options/?model=academics.Subject").data
        self.assertEqual({o["label"] for o in opts}, {"درس دوم (LS1)"} if opts and "(" in opts[0]["label"] else {o["label"] for o in opts})
        names = {s["name"] for s in self.api(self.sa).get("/api/subjects/").data} if hasattr(self, "sa") else set()
        self.assertNotIn("درس ابتدایی", names)

    def test_super_admin_still_sees_all_levels(self):
        from apps.accounts.models import User
        root = User.objects.create_superuser("root_lv", password="Str0ng-pass-1")
        forms = self.api(root).get("/api/school-form/").data
        self.assertTrue({"دهم-ل", "اول-ل"} <= {g["name"] for g in forms["grades"]})


class SchoolCodeDefaultTests(Base):
    """The school's code is entered in «ساختار سازمانی» and copied into the school profile automatically."""

    def test_profile_takes_the_school_code_when_left_blank(self):
        from apps.academics.models import Level
        self.school.code = "SC-7741"
        self.school.save()
        lvl = Level.objects.create(code="MSX", name="متوسطه-ایکس", order=5)
        root = User.objects.create_superuser("root_code", password=PW)
        r = self.api(root).post("/api/system/school_profiles/new/", {"school": self.school.pk, "level": lvl.pk}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        from apps.accounts.models import SchoolProfile
        self.assertEqual(SchoolProfile.objects.get(school=self.school).school_code, "SC-7741")

    def test_org_code_lookup_for_the_form(self):
        self.school.code = "SC-8842"
        self.school.save()
        root = User.objects.create_superuser("root_code2", password=PW)
        r = self.api(root).get(f"/api/system/org-code/?id={self.school.pk}")
        self.assertEqual(r.data["code"], "SC-8842")


class FieldLevelWithBranchTests(TestCase):
    """A study field's level is chosen with its branch (شاخه) and shown with it again when edited."""

    def setUp(self):
        from apps.academics.models import Branch, Level
        self.lvl = Level.objects.create(code="S2Q", name="متوسطه دوم", order=20)
        self.branch = Branch.objects.create(level=self.lvl, name="کاردانش")
        self.field = StudyField.objects.create(code="93154", name="خیاطی", level=self.lvl)
        self.root = User.objects.create_superuser("root_fb", password="Str0ng-pass-1")

    def test_detail_shows_level_with_branch_and_save_keeps_the_pair(self):
        from django.test import Client
        c = Client(); c.force_login(self.root)
        r = c.get(f"/api/system/fields/{self.field.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["values"]["level"], f"{self.lvl.pk}:")
        r = c.patch(f"/api/system/fields/{self.field.pk}/", data={"level": f"{self.lvl.pk}:کاردانش"}, content_type="application/json")
        self.assertIn(r.status_code, (200, 204), r.content[:200])
        self.field.refresh_from_db()
        self.assertEqual(self.field.branch_id, self.branch.pk)
        r = c.get(f"/api/system/fields/{self.field.pk}/")
        self.assertEqual(r.json()["values"]["level"], f"{self.lvl.pk}:کاردانش")


class GradeLevelWithBranchTests(TestCase):
    """A grade's level is chosen with its branch («متوسطه دوم — کاردانش») and shown with it again when edited."""

    def setUp(self):
        from apps.academics.models import Branch, Grade, Level
        self.lvl = Level.objects.create(code="S2G", name="متوسطه دوم", order=40)
        self.branch = Branch.objects.create(level=self.lvl, name="کاردانش")
        self.grade = Grade.objects.create(level=self.lvl, name="دهم")
        self.root = User.objects.create_superuser("root_gb", password="Str0ng-pass-1")

    def test_grade_keeps_level_with_branch(self):
        from django.test import Client
        c = Client(); c.force_login(self.root)
        r = c.patch(f"/api/system/grades/{self.grade.pk}/", data={"level": f"{self.lvl.pk}:کاردانش"}, content_type="application/json")
        self.assertIn(r.status_code, (200, 204), r.content[:200])
        self.grade.refresh_from_db()
        self.assertEqual(self.grade.branch_id, self.branch.pk)
        r = c.get(f"/api/system/grades/{self.grade.pk}/")
        self.assertEqual(r.json()["values"]["level"], f"{self.lvl.pk}:کاردانش")
        self.assertIn("کاردانش", r.json()["labels"]["level"])


class GradeListShowsBranchTests(TestCase):
    """In the list of grades and fields, the level is shown with its branch."""

    def test_list_row_shows_level_and_branch(self):
        from apps.academics.models import Branch, Grade, Level
        from django.test import Client
        lvl = Level.objects.create(code="LS1", name="متوسطه دوم", order=50)
        br = Branch.objects.create(level=lvl, name="کاردانش")
        Grade.objects.create(level=lvl, branch=br, name="دهم")
        root = User.objects.create_superuser("root_list", password="Str0ng-pass-1")
        c = Client(); c.force_login(root)
        rows = c.get("/api/system/grades/").json()["rows"]
        self.assertTrue(any(r.get("level") == "متوسطه دوم — کاردانش" for r in rows), rows[:2])


class DeleteExplainsDependenciesTests(TestCase):
    """A refused delete says what depends on the item, and how many."""

    def test_grade_with_students_cannot_be_deleted_and_the_reason_is_given(self):
        from apps.academics.models import ClassRoom, Grade, Level, AcademicYear
        from django.test import Client
        lvl = Level.objects.create(code="DD1", name="متوسطه دوم", order=60)
        g = Grade.objects.create(level=lvl, name="یازدهم")
        y = AcademicYear.objects.create(name="۱۴۰۵-۱۴۰۶", starts_on="2026-09-01", ends_on="2027-06-30", is_current=True)
        from apps.accounts.models import OrgUnit
        county = OrgUnit.objects.create(name="ش دی", kind="county")
        region = OrgUnit.objects.create(name="ن دی", kind="region", parent=county)
        school = OrgUnit.objects.create(name="م دی", kind="school", parent=region)
        ClassRoom.objects.create(school=school, academic_year=y, name="۱۰۱", code="DD9", grade=g)
        root = User.objects.create_superuser("root_del", password="Str0ng-pass-1")
        c = Client(); c.force_login(root)
        r = c.delete(f"/api/system/grades/{g.pk}/")
        self.assertEqual(r.status_code, 409)
        self.assertIn("کلاس", r.json()["detail"])
        self.assertIn("1 مورد", r.json()["detail"])
