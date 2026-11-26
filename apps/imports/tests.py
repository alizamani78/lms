from django.utils import timezone
from datetime import timedelta

from apps.academics.models import ClassRoom
from apps.accounts.models import SchoolMembership, User
from apps.core.tests_phase2 import Base

from .parsers import parse
from .models import ImportJob


class ParserTests(Base):
    def test_seyda_headers_and_arabic_letters(self):
        text = "پایه\tنوع کلاس\tکلاس\tکد دانش آموز\tنام\tنام خانوادگی\n" \
               "هشتم\tعادی\tرودکی 802\t970487177\tبنيامين\tپيراسته\n"
        rows, unknown = parse("students", text=text)
        self.assertEqual(rows[0]["values"]["first_name"], "بنیامین")
        self.assertEqual(rows[0]["values"]["class_title"], "رودکی 802")
        self.assertIn("نوع کلاس", unknown)

    def test_tel_link_removed(self):
        rows, _ = parse("students", text="کد دانش آموز\tنام\tنام خانوادگی\tکلاس\n[970487177](tel:970487177)\tعلی\tاحمدی\tرودکی 802\n")
        self.assertEqual(rows[0]["values"]["student_code"], "970487177")


class ImportFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.school2_admin = User.objects.create_user("sa_b", password="Str0ng-pass-1", role="school_admin", org=self.school2)
        self.classroom.code = "6664686"
        self.classroom.save()

    def upload(self, user, kind, text):
        return self.api(user).post("/api/imports/create/", {"kind": kind, "school": self.school.pk, "text": text})

    def test_preview_marks_good_and_bad_rows_and_duplicates(self):
        text = ("کد دانش آموز\tنام\tنام خانوادگی\tکلاس\tکد ملی\n"
                "960000001\tعلی\tاکبری\tدهم الف\t\n"          # ok (class exists in fixture)
                "960000001\tرضا\tمحمدی\tدهم الف\t\n"          # duplicate in file
                "960000002\tحسن\tحسینی\tکلاس نامعلوم\t\n"     # class missing
                "960000003\tمریم\tکاظمی\tدهم الف\t1234567890\n")  # bad national id checksum
        r = self.upload(self.sa, "students", text)
        self.assertEqual(r.status_code, 201)
        st = {row["n"]: row["status"] for row in r.data["rows"]}
        self.assertEqual(st, {1: "ok", 2: "error", 3: "error", 4: "error"})
        self.assertIn("تکراری", " ".join(r.data["rows"][1]["errors"]))

    def test_correct_then_commit_only_green(self):
        text = "کد دانش آموز\tنام\tنام خانوادگی\tکلاس\n960000001\tعلی\tاکبری\tدهم الف\n960000009\tنفر\tبد\tکلاس نامعلوم\n"
        job = self.upload(self.sa, "students", text).data
        fixed = self.api(self.sa).patch(f"/api/imports/{job['id']}/rows/",
                                        {"rows": [{"n": 2, "values": {"class_title": "دهم الف"}}]}, format="json")
        self.assertEqual(fixed.data["summary"]["ok"], 2)
        done = self.api(self.sa).post(f"/api/imports/{job['id']}/commit/")
        self.assertEqual(done.data["summary"]["created"], 2)
        self.assertTrue(User.objects.filter(student_code="960000001", org=self.school).exists())
        self.assertTrue(self.classroom.students.filter(student_code="960000009").exists())
        self.assertEqual(self.api(self.sa).post(f"/api/imports/{job['id']}/commit/").status_code, 409)

    def test_teachers_and_membership_and_scope(self):
        nid = "0499370899"       # valid checksum
        text = "کد ملی\tنام\tنام خانوادگی\tموبایل\n" + f"{nid}\tزهرا\tرضایی\t09121234567\n"
        job = self.upload(self.sa, "teachers", text).data
        self.assertEqual(job["summary"]["ok"], 1)
        self.api(self.sa).post(f"/api/imports/{job['id']}/commit/")
        t = User.objects.get(username=nid)
        self.assertTrue(SchoolMembership.objects.filter(user=t, school=self.school).exists())
        self.assertFalse(t.has_usable_password())
        # a school_admin of school B cannot import into school A
        self.assertEqual(self.api(self.school2_admin).post("/api/imports/create/", {"kind": "teachers", "school": self.school.pk,
                                                                                   "text": text}).status_code, 404)

    def test_multi_school_principal_sees_both(self):
        SchoolMembership.objects.create(user=self.sa, school=self.school2)
        names = {s["id"] for s in self.api(self.sa).get("/api/me/schools/").data}
        self.assertEqual(names, {self.school.pk, self.school2.pk})
        text = "کد دانش آموز\tنام\tنام خانوادگی\tکلاس\n960000050\tاو\tب\tدهم الف\n"
        r = self.api(self.sa).post("/api/imports/create/", {"kind": "students", "school": self.school2.pk, "text": text})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data["summary"]["error"], 1)          # class is in school A, not school B

    def test_reset_password_shown_once_and_scoped(self):
        u = User.objects.create_user("stu1", password="Str0ng-pass-1", role="student", org=self.school)
        r = self.api(self.sa).post(f"/api/users/{u.pk}/reset-password/")
        self.assertEqual(r.status_code, 200)
        from rest_framework.test import APIClient
        login = APIClient().post("/api/auth/login/", {"username": "stu1", "password": r.data["temporary_password"]}, format="json")
        self.assertEqual(login.status_code, 200)
        self.assertEqual(self.api(self.school2_admin).post(f"/api/users/{u.pk}/reset-password/").status_code, 404)
        self.assertEqual(self.api(self.s1).post(f"/api/users/{u.pk}/reset-password/").status_code, 403)
