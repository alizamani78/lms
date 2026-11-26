import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.academics.models import AcademicYear, ClassRoom, Grade, Level
from apps.accounts.mfa import _hotp, encrypt, new_secret
from apps.accounts.models import OrgUnit, ParentLink, TOTPDevice, User
from apps.attendance.models import AttendanceChange, AttendanceRecord
from apps.core.models import GradeChange
from apps.courses.models import Course, Enrollment
from apps.exams.models import Answer, Attempt, Choice, Exam
from apps.integrations.models import ExternalSystem
from apps.liveclass.models import ClassSession, OnlinePresence
from apps.liveclass.providers import PROVIDERS, PermanentProviderError
from apps.liveclass.tasks import finalize_ended_sessions
from apps.reports.tasks import build_daily_stats

PW = "Str0ng-pass-1"


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.prov = OrgUnit.objects.create(name="P", kind="province")
        self.reg = OrgUnit.objects.create(name="R", kind="region", parent=self.prov)
        self.school = OrgUnit.objects.create(name="A", kind="school", parent=self.reg)
        self.school2 = OrgUnit.objects.create(name="B", kind="school", parent=self.reg)
        mk = lambda n, role, org: User.objects.create_user(n, password=PW, role=role, org=org)
        self.t1, self.t2 = mk("t1", "teacher", self.school), mk("t2", "teacher", self.school2)
        self.s1, self.s2, self.s3 = (mk(n, "student", self.school) for n in ("s1", "s2", "s3"))
        self.s4 = mk("s4", "student", self.school2)
        self.sa, self.ra = mk("sa", "school_admin", self.school), mk("ra", "region_admin", self.reg)
        self.parent = mk("par", "parent", self.school)
        self.year = AcademicYear.objects.create(name="1405", starts_on=date(2026, 9, 23), ends_on=date(2027, 6, 22),
                                                is_current=True)
        level = Level.objects.create(name="متوسطه دوم", order=3)
        self.grade = Grade.objects.create(level=level, name="دهم", order=1)
        self.classroom = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="دهم الف",
                                                  grade=self.grade, homeroom_teacher=self.t1)
        self.classroom.students.set([self.s1, self.s2, self.s3])
        self.course = Course.objects.create(org=self.school, title="ریاضی", teacher=self.t1, classroom=self.classroom)
        for s in (self.s1, self.s2, self.s3):
            Enrollment.objects.create(course=self.course, student=s)

    def api(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def exam_payload(self, questions, **extra):
        now = timezone.now()
        d = {"title": "e", "starts_at": (now - timedelta(minutes=1)).isoformat(),
             "ends_at": (now + timedelta(hours=1)).isoformat(), "duration_minutes": 20, "show_results": True,
             "questions": questions}
        d.update(extra)
        return d

    def make_exam(self, questions, **extra):
        t = self.api(self.t1)
        eid = t.post(f"/api/courses/{self.course.pk}/exams/", self.exam_payload(questions, **extra), format="json").data["id"]
        self.assertEqual(t.post(f"/api/exams/{eid}/publish/", {"published": True}, format="json").status_code, 200)
        return eid


class AcademicYearTests(Base):
    def test_year_flows_into_course_and_enrollment(self):
        self.assertEqual(self.course.academic_year_id, self.year.pk)
        e = Enrollment.objects.get(course=self.course, student=self.s1)
        self.assertEqual(e.academic_year_id, self.year.pk)


class QuestionKindTests(Base):
    QS = [{"kind": "mcq", "text": "q1", "points": "2", "choices": [{"text": "a", "is_correct": True}, {"text": "b"}]},
          {"kind": "tf", "text": "q2", "points": "1", "tf_answer": True},
          {"kind": "essay", "text": "q3", "points": "5"}]

    def test_essay_flow_and_grade_history(self):
        eid = self.make_exam(self.QS)
        st = self.api(self.s1)
        start = st.post(f"/api/exams/{eid}/start/")
        self.assertNotIn("is_correct", start.content.decode())
        attempt = start.data["attempt"]
        q = {x["text"]: x["id"] for x in start.data["questions"]}
        ok1 = Choice.objects.get(question__text="q1", is_correct=True).pk
        ok2 = Choice.objects.get(question__text="q2", is_correct=True).pk
        put = lambda body: st.put(f"/api/attempts/{attempt}/answer/", body, format="json").status_code
        self.assertEqual(put({"question": q["q1"], "choice": ok1}), 204)
        self.assertEqual(put({"question": q["q2"], "choice": ok2}), 204)
        self.assertEqual(put({"question": q["q3"], "text": "پاسخ تشریحی"}), 204)
        r = st.post(f"/api/attempts/{attempt}/submit/")
        self.assertEqual((r.data["score"], r.data["total"], r.data["pending_review"]), (3.0, 8.0, True))
        t = self.api(self.t1)
        essays = t.get(f"/api/exams/{eid}/essays/?pending=1").data
        self.assertEqual(len(essays), 1)
        aid = essays[0]["id"]
        self.assertEqual(t.post(f"/api/answers/{aid}/grade/", {"points": "4"}, format="json").status_code, 200)
        self.assertEqual(float(Attempt.objects.get(pk=attempt).score), 7.0)
        self.assertEqual(Attempt.objects.get(pk=attempt).grading_status, "graded")
        self.assertEqual(t.post(f"/api/answers/{aid}/grade/", {"points": "5"}, format="json").status_code, 400)  # no reason
        self.assertEqual(t.post(f"/api/answers/{aid}/grade/", {"points": "5", "reason": "بازبینی"}, format="json").status_code, 200)
        self.assertEqual(t.post(f"/api/answers/{aid}/grade/", {"points": "9", "reason": "x"}, format="json").status_code, 400)
        hist = list(GradeChange.objects.filter(kind="answer", ref_id=aid).order_by("id").values_list("old_points", "new_points"))
        self.assertEqual([(None if o is None else float(o), float(n)) for o, n in hist], [(None, 4.0), (4.0, 5.0)])
        self.assertEqual(self.api(self.s1).post(f"/api/answers/{aid}/grade/", {"points": "1"}, format="json").status_code, 403)
        self.assertEqual(self.api(self.t2).post(f"/api/answers/{aid}/grade/", {"points": "1"}, format="json").status_code, 404)

    def test_random_draw_is_stable_and_enforced(self):
        qs = [{"text": f"q{i}", "choices": [{"text": "a", "is_correct": True}, {"text": "b"}]} for i in range(4)]
        eid = self.make_exam(qs, draw_count=2, shuffle_questions=True, shuffle_choices=True)
        c = self.api(self.s1)
        first = c.post(f"/api/exams/{eid}/start/").data
        again = c.post(f"/api/exams/{eid}/start/").data
        ids = [q["id"] for q in first["questions"]]
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids, [q["id"] for q in again["questions"]])
        outside = Exam.objects.get(pk=eid).questions.exclude(pk__in=ids).first().pk
        r = c.put(f"/api/attempts/{first['attempt']}/answer/", {"question": outside, "choice": 1}, format="json")
        self.assertEqual(r.status_code, 400)
        r = c.post(f"/api/attempts/{first['attempt']}/submit/")
        self.assertEqual(r.data["total"], 2.0)


class BankTests(Base):
    def test_bank_visibility_and_copy(self):
        t = self.api(self.t1)
        body = {"kind": "mcq", "text": "bq", "points": "1", "choices": [{"text": "a", "is_correct": True}, {"text": "b"}]}
        r = t.post("/api/bank/", body, format="json")
        self.assertEqual(r.status_code, 201)
        bid = r.data["id"]
        self.assertEqual(len(t.get("/api/bank/").data), 1)
        self.assertEqual(self.api(self.t2).get("/api/bank/").data, [])                  # other school: not visible
        self.assertEqual(len(self.api(self.sa).get("/api/bank/").data), 1)              # admin above sees it
        eid = self.api(self.t1).post(f"/api/courses/{self.course.pk}/exams/",
                                     self.exam_payload([]), format="json").data["id"]
        r = t.post(f"/api/exams/{eid}/add-from-bank/", {"bank_ids": [bid]}, format="json")
        self.assertEqual(r.data["added"], 1)
        self.assertEqual(Exam.objects.get(pk=eid).questions.first().choices.count(), 2)
        self.assertEqual(self.api(self.t2).post(f"/api/exams/{eid}/add-from-bank/", {"bank_ids": [bid]}, format="json").status_code, 404)


class AssignmentTests(Base):
    def test_assignment_lifecycle(self):
        t, now = self.api(self.t1), timezone.now()
        r = t.post(f"/api/courses/{self.course.pk}/assignments/",
                   {"title": "تمرین", "due_at": (now + timedelta(days=1)).isoformat(), "max_points": "20"}, format="json")
        aid = r.data["id"]
        st = self.api(self.s1)
        self.assertEqual(st.post(f"/api/assignments/{aid}/submit/", {"text": "x"}, format="json").status_code, 404)  # unpublished
        t.patch(f"/api/assignments/{aid}/", {"is_published": True}, format="json")
        self.assertEqual(st.post(f"/api/assignments/{aid}/submit/", {"text": "x"}, format="json").status_code, 201)
        self.assertEqual(st.post(f"/api/assignments/{aid}/submit/", {"text": "y"}, format="json").status_code, 200)
        self.assertEqual(st.post(f"/api/assignments/{aid}/submit/", {"file_url": "javascript:alert(1)"}, format="json").status_code, 400)
        self.assertEqual(self.api(self.s4).post(f"/api/assignments/{aid}/submit/", {"text": "x"}, format="json").status_code, 404)
        sub = t.get(f"/api/assignments/{aid}/submissions/").data[0]
        self.assertEqual(t.post(f"/api/submissions/{sub['id']}/grade/", {"points": "15"}, format="json").status_code, 200)
        self.assertEqual(t.post(f"/api/submissions/{sub['id']}/grade/", {"points": "17"}, format="json").status_code, 400)
        self.assertEqual(t.post(f"/api/submissions/{sub['id']}/grade/", {"points": "17", "reason": "اصلاح"}, format="json").status_code, 200)
        self.assertEqual(GradeChange.objects.filter(kind="submission", ref_id=sub["id"]).count(), 2)
        self.assertEqual(st.post(f"/api/assignments/{aid}/submit/", {"text": "z"}, format="json").status_code, 409)  # graded => locked
        mine = st.get("/api/assignments/").data[0]["submission"]
        self.assertEqual(mine["points"], 17.0)

    def test_late_submission_rules(self):
        from apps.coursework.models import Assignment
        a = Assignment.objects.create(course=self.course, title="old", due_at=timezone.now() - timedelta(hours=1),
                                      is_published=True)
        st = self.api(self.s1)
        self.assertEqual(st.post(f"/api/assignments/{a.pk}/submit/", {"text": "x"}, format="json").status_code, 403)
        Assignment.objects.filter(pk=a.pk).update(allow_late=True)
        r = st.post(f"/api/assignments/{a.pk}/submit/", {"text": "x"}, format="json")
        self.assertEqual((r.status_code, r.data["is_late"]), (201, True))


class AttendanceTests(Base):
    def body(self, status="absent", **extra):
        d = {"classroom": self.classroom.pk, "date": timezone.localdate().isoformat(),
             "records": [{"student": self.s1.pk, "status": status}, {"student": self.s2.pk, "status": "present"}]}
        d.update(extra)
        return d

    def test_record_edit_history_and_limits(self):
        t = self.api(self.t1)                                    # homeroom teacher
        self.assertEqual(t.post("/api/attendance/", self.body(), format="json").data["created"], 2)
        r = t.post("/api/attendance/", self.body("present"), format="json")        # correction without reason
        self.assertEqual(r.status_code, 400)
        r = t.post("/api/attendance/", self.body("excused", reason="گواهی پزشکی"), format="json")
        self.assertEqual(r.data["changed"], 1)
        ch = AttendanceChange.objects.get()
        self.assertEqual((ch.old_status, ch.new_status, ch.reason, ch.changed_by_id), ("absent", "excused", "گواهی پزشکی", self.t1.pk))
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertEqual(t.post("/api/attendance/", self.body(date=future), format="json").status_code, 400)
        outsider = self.body(); outsider["records"][0]["student"] = self.s4.pk
        self.assertEqual(t.post("/api/attendance/", outsider, format="json").status_code, 400)       # not in roster
        self.assertEqual(self.api(self.t2).post("/api/attendance/", self.body(), format="json").status_code, 404)
        self.assertEqual(self.api(self.s1).post("/api/attendance/", self.body(), format="json").status_code, 403)

    def test_student_sees_only_own(self):
        self.api(self.t1).post("/api/attendance/", self.body(), format="json")
        d = self.api(self.s1).get("/api/me/attendance/").data
        self.assertEqual((d["summary"]["absent"], len(d["records"])), (1, 1))


class ParentTests(Base):
    def test_parent_sees_only_verified_children(self):
        ParentLink.objects.create(parent=self.parent, student=self.s1, is_verified=True)
        ParentLink.objects.create(parent=self.parent, student=self.s3, is_verified=False)
        p = self.api(self.parent)
        self.assertEqual([c["id"] for c in p.get("/api/children/").data], [self.s1.pk])
        self.assertEqual(p.get(f"/api/children/{self.s1.pk}/overview/").status_code, 200)
        self.assertEqual(p.get(f"/api/children/{self.s2.pk}/overview/").status_code, 404)      # not linked
        self.assertEqual(p.get(f"/api/children/{self.s3.pk}/overview/").status_code, 404)      # not verified
        self.assertEqual(p.get("/api/courses/").data["count"], 0)                              # no org-scope leak
        self.assertEqual(p.get("/api/exams/").data, [])
        self.assertEqual(p.get("/api/assignments/").data, [])
        self.assertEqual(p.get("/api/attendance/?classroom=1").status_code, 403)
        self.assertEqual(self.api(self.s1).get("/api/children/").status_code, 403)


def _code(secret, offset=0):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    return _hotp(key, int(time.time()) // 30 + offset)


class MfaTests(Base):
    def login(self, c, user, **extra):
        return c.post("/api/auth/login/", {"username": user.username, "password": PW, **extra}, format="json")

    def test_login_requires_otp_and_blocks_replay(self):
        secret = new_secret()
        TOTPDevice.objects.create(user=self.sa, secret_enc=encrypt(secret), confirmed=True)
        c = APIClient()
        r = self.login(c, self.sa)
        self.assertEqual((r.status_code, "otp" in r.data), (400, True))
        self.assertEqual(self.login(c, self.sa, otp="000000").status_code, 401)
        code = _code(secret)
        r = self.login(c, self.sa, otp=code)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(AccessToken(r.data["access"])["mfa"])
        self.assertEqual(self.login(c, self.sa, otp=code).status_code, 401)          # same code cannot be replayed

    @override_settings(MFA_ENFORCE=True)
    def test_required_role_blocked_until_mfa_verified(self):
        c = APIClient()
        r = self.login(c, self.sa)
        self.assertEqual(r.status_code, 200)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        self.assertEqual(c.get("/api/courses/").status_code, 401)                    # MFA_REQUIRED
        me = c.get("/api/me/").data
        self.assertEqual((me["mfa_required"], me["mfa_verified"]), (True, False))
        secret = c.post("/api/mfa/setup/").data["secret"]
        r = c.post("/api/mfa/confirm/", {"code": _code(secret)}, format="json")
        self.assertEqual(r.status_code, 200)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        self.assertEqual(c.get("/api/courses/").status_code, 200)
        self.assertEqual(c.post("/api/mfa/disable/", {"password": PW, "code": "123456"}, format="json").status_code, 403)

    @override_settings(MFA_ENFORCE=True)
    def test_students_are_not_forced_into_mfa(self):
        c = APIClient()
        r = self.login(c, self.s1)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        self.assertEqual(c.get("/api/courses/").status_code, 200)


@mock.patch.dict(os.environ, {"HOOK_SECRET": "s3cret"})
class LiveClassTests(Base):
    def setUp(self):
        super().setUp()
        self.system = ExternalSystem.objects.create(name="bbb1", kind="bbb", base_url="https://bbb.example.ir/bigbluebutton",
                                                    secret_env="HOOK_SECRET")
        now = timezone.now()
        self.start, self.end = now - timedelta(hours=2), now - timedelta(hours=1)
        self.session = ClassSession.objects.create(course=self.course, title="جلسه", provider="bbb", system=self.system,
                                                   external_id="m1", starts_at=self.start, ends_at=self.end)

    def post(self, payload, ts=None, sig=None, secret="s3cret"):
        body = json.dumps(payload).encode()
        ts = str(int(time.time())) if ts is None else ts
        sig = sig or "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return self.client.post(f"/api/hooks/{self.system.pk}/", data=body, content_type="application/json",
                                HTTP_X_TIMESTAMP=ts, HTTP_X_SIGNATURE=sig)

    def events(self):
        mk = lambda i, t, u, ts: {"id": i, "type": t, "meeting_id": "m1", "user_id": str(u), "ts": ts.isoformat()}
        return {"events": [mk("e1", "user_joined", self.s1.pk, self.start), mk("e2", "user_left", self.s1.pk, self.end)]}

    def test_webhook_security_and_idempotency(self):
        self.assertEqual(self.post(self.events(), sig="sha256=bad").status_code, 401)
        self.assertEqual(self.post(self.events(), ts=str(int(time.time()) - 3600)).status_code, 401)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(self.events()).data["accepted"], 2)
        self.assertEqual(self.post(self.events()).data["accepted"], 0)                      # duplicate delivery
        self.assertEqual(OnlinePresence.objects.get(student=self.s1).total_seconds, 3600)

    def test_attendance_from_provider_and_no_fabrication(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.post(self.events())
        silent = ClassSession.objects.create(course=self.course, title="بی‌گزارش", provider="bbb", system=self.system,
                                             external_id="m2", starts_at=self.start - timedelta(days=1),
                                             ends_at=self.end - timedelta(days=1))
        self.assertEqual(finalize_ended_sessions(), 2)
        self.session.refresh_from_db(); silent.refresh_from_db()
        self.assertEqual((self.session.attendance_status, silent.attendance_status), ("finalized", "no_data"))
        rec = {r.student_id: r for r in AttendanceRecord.objects.filter(session=self.session)}
        self.assertEqual((rec[self.s1.pk].status, rec[self.s1.pk].source, rec[self.s1.pk].validation),
                         ("present", "online_provider", "validated"))
        self.assertEqual(rec[self.s2.pk].status, "absent")
        self.assertFalse(AttendanceRecord.objects.filter(session=silent).exists())          # nothing invented

    def test_auto_create_queue(self):
        kw = dict(course=self.course, title="x", provider="bbb", system=self.system, auto_create=True,
                  starts_at=timezone.now() + timedelta(hours=1), ends_at=timezone.now() + timedelta(hours=2))
        with mock.patch.object(PROVIDERS["bbb"], "create_meeting", return_value=None) as m:
            with self.captureOnCommitCallbacks(execute=True):
                s = ClassSession.objects.create(**kw)
        s.refresh_from_db()
        self.assertEqual((s.remote_status, s.external_id.startswith("lms-")), ("ready", True))
        m.assert_called_once()
        with mock.patch.object(PROVIDERS["bbb"], "create_meeting", side_effect=PermanentProviderError("no")):
            with self.captureOnCommitCallbacks(execute=True):
                bad = ClassSession.objects.create(**kw)
        bad.refresh_from_db()
        self.assertEqual(bad.remote_status, "failed")
        self.assertEqual(self.api(self.t1).post(f"/api/live/{bad.pk}/join/").status_code, 409)


class StatsTests(Base):
    def test_daily_stats_rollup_and_scope(self):
        today = timezone.localdate()
        for st, status in ((self.s1, "present"), (self.s2, "absent")):
            AttendanceRecord.objects.create(student=st, classroom=self.classroom, date=today, slot_key="c0p0", status=status)
        exam = Exam.objects.create(course=self.course, title="e", starts_at=timezone.now(), ends_at=timezone.now() + timedelta(hours=1))
        Attempt.objects.create(exam=exam, student=self.s1, deadline=timezone.now(), submitted_at=timezone.now(), score=3, total=4)
        self.assertEqual(build_daily_stats(today.isoformat()), 2)
        d = self.api(self.ra).get(f"/api/reports/orgs/{self.reg.pk}/stats/?days=7").data
        row = {c["name"]: c for c in d["children"]}["A"]
        self.assertEqual((row["students"], row["attendance_rate"], row["avg_exam_percent"]), (3, 50.0, 75.0))
        self.assertEqual(self.api(self.ra).get(f"/api/reports/orgs/{self.prov.pk}/stats/").status_code, 404)
        self.assertEqual(self.api(self.t1).get(f"/api/reports/orgs/{self.reg.pk}/stats/").status_code, 403)


class MultipartSupportTests(Base):
    """Regression: multipart/form-data must be accepted by every endpoint that takes a file."""

    def test_import_create_accepts_multipart_text(self):
        from django.test import Client
        c = self.api(self.sa)
        r = c.post("/api/imports/create/", {"kind": "students", "school": self.school.pk,
                                           "text": "کد دانش آموز\tنام\tنام خانوادگی\tکلاس\n960000777\tعلی\tاحمدی\tدهم الف\n"},
                   format="multipart")
        self.assertNotEqual(r.status_code, 415)
        self.assertEqual(r.status_code, 201, r.data if hasattr(r, "data") else r.content)
