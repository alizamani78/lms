import io
import zipfile

from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import User
from apps.accounts.models import OrgUnit
from apps.core.persian import canonical, is_valid_national_id, normalize_text
from apps.coursework.models import Assignment, Submission
from apps.files.models import FileAsset
from apps.files.validators import check_header
from apps.core.tests_phase2 import Base

import tempfile
TMP = tempfile.mkdtemp()


class PersianTests(Base):
    def test_normalisation(self):
        self.assertEqual(normalize_text("بنيامين  ك\u200f"), "بنیامین ک")
        self.assertEqual(normalize_text("[970487177](tel:970487177)"), "970487177")
        self.assertEqual(canonical("عبدي"), canonical("عبدی"))
        self.assertEqual(normalize_text("۱۲۳٤"), "1234")

    def test_national_id_checksum(self):
        self.assertTrue(is_valid_national_id("0499370899"))      # valid checksum
        self.assertFalse(is_valid_national_id("0499370898"))
        self.assertFalse(is_valid_national_id("1111111111"))     # all same digits
        self.assertFalse(is_valid_national_id("12345"))


def upload(client, name, data):
    return client.post("/api/files/", {"file": SimpleUploadedFile(name, data)}, format="multipart")


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class FileTests(Base):
    def test_upload_checks_and_access(self):
        t = self.api(self.t1)
        pdf = upload(t, "sheet.pdf", b"%PDF-1.4 hi")
        self.assertEqual(pdf.status_code, 201)
        self.assertEqual(upload(t, "evil.exe", b"MZ..").status_code, 400)                 # not allowed type
        self.assertEqual(upload(t, "fake.pdf", b"not a pdf").status_code, 400)            # content mismatch
        bad_zip = io.BytesIO()
        with zipfile.ZipFile(bad_zip, "w") as z:
            z.writestr("run.exe", b"x")
        self.assertEqual(upload(t, "pack.zip", bad_zip.getvalue()).status_code, 400)      # executable inside zip
        self.assertEqual(upload(self.api(self.parent), "x.pdf", b"%PDF").status_code, 403)
        fid = pdf.data["id"]
        self.assertEqual(self.api(self.t1).get(f"/api/files/{fid}/download/").status_code, 200)
        self.assertEqual(self.api(self.t2).get(f"/api/files/{fid}/download/").status_code, 404)    # other school

    def test_content_visibility_follows_publish_and_enrolment(self):
        asset = FileAsset.objects.get(pk=upload(self.api(self.t1), "a.pdf", b"%PDF-1.4").data["id"])
        item = self.api(self.t1).post(f"/api/courses/{self.course.pk}/content/",
                                      {"title": "جزوه", "kind": "document", "file": asset.pk, "is_published": False}, format="json")
        self.assertEqual(item.status_code, 201)
        self.assertEqual(self.api(self.s1).get(f"/api/files/{asset.pk}/download/").status_code, 404)   # draft
        self.api(self.t1).patch(f"/api/content/{item.data['id']}/", {"is_published": True}, format="json")
        self.assertEqual(self.api(self.s1).get(f"/api/files/{asset.pk}/download/").status_code, 200)
        self.assertEqual(self.api(self.s4).get(f"/api/files/{asset.pk}/download/").status_code, 404)  # not enrolled

    def test_student_cannot_use_someone_elses_file(self):
        mine = FileAsset.objects.get(pk=upload(self.api(self.s1), "h.docx", open_docx()).data["id"])
        self.assertEqual(self.api(self.s2).get(f"/api/files/{mine.pk}/download/").status_code, 404)


def open_docx():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr("word/document.xml", "<w/>")
    return b.getvalue()


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class AssignmentTargetTests(Base):
    def test_selected_students_and_privacy_of_submissions(self):
        now = timezone.now()
        t = self.api(self.t1)
        r = t.post(f"/api/courses/{self.course.pk}/assignments/",
                   {"title": "تمرین ویژه", "due_at": (now + timedelta(days=2)).isoformat(), "audience": "selected",
                    "students": [self.s1.pk]}, format="json")
        self.assertEqual(r.status_code, 201)
        aid = r.data["id"]
        t.patch(f"/api/assignments/{aid}/", {"is_published": True}, format="json")
        self.assertEqual(len(self.api(self.s1).get("/api/assignments/").data), 1)
        self.assertEqual(self.api(self.s2).get("/api/assignments/").data, [])            # not targeted
        self.assertEqual(self.api(self.s2).post(f"/api/assignments/{aid}/submit/", {"text": "x"}, format="json").status_code, 404)
        self.assertEqual(self.api(self.s1).post(f"/api/assignments/{aid}/submit/", {"text": "پاسخ"}, format="json").status_code, 201)
        sub = Submission.objects.get(assignment_id=aid, student=self.s1)
        self.assertEqual(self.api(self.sa).get(f"/api/assignments/{aid}/submissions/").status_code, 404)  # admin cannot read
        self.assertEqual(self.api(self.parent).get("/api/assignments/").data, [])
        self.assertEqual(len(t.get(f"/api/assignments/{aid}/submissions/").data), 1)
        self.assertEqual(self.api(self.t2).get(f"/api/assignments/{aid}/submissions/").status_code, 404)
        self.assertEqual(self.api(self.s1).post(f"/api/submissions/{sub.pk}/grade/", {"points": "1"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.s2).post(f"/api/assignments/{aid}/submit/", {"text": "x"}, format="json").status_code, 404)

    def test_selected_students_must_be_enrolled(self):
        r = self.api(self.t1).post(f"/api/courses/{self.course.pk}/assignments/",
                                   {"title": "x", "due_at": (timezone.now() + timedelta(days=1)).isoformat(),
                                    "audience": "selected", "students": [self.s4.pk]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_teacher_content_creation_is_scoped(self):
        self.assertEqual(self.api(self.sa).post(f"/api/courses/{self.course.pk}/content/",
                                                {"title": "x", "kind": "text", "body": "y"}, format="json").status_code, 403)
