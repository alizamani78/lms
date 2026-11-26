from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone

from apps.academics.models import ClassRoom
from apps.accounts.models import ParentLink, User
from apps.attendance.models import AttendanceRecord
from apps.core.models import GradeChange
from apps.coursework.models import Assignment, Submission
from apps.courses.models import Course, Enrollment
from apps.exams.models import Answer, Attempt
from apps.schoolcal.models import CalendarEvent

from .tests_phase2 import Base, PW


class CalendarTests(Base):
    def test_holiday_blocks_manual_attendance(self):
        today = timezone.localdate()
        body = {"classroom": self.classroom.pk, "date": today.isoformat(),
                "records": [{"student": self.s1.pk, "status": "present"}]}
        CalendarEvent.objects.create(org=None, title="تعطیل رسمی", kind="holiday", start_date=today, end_date=today,
                                     blocks_attendance=True)
        r = self.api(self.t1).post("/api/attendance/", body, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("تعطیل رسمی", str(r.data))
        body["date"] = (today - timedelta(days=1)).isoformat()
        self.assertEqual(self.api(self.t1).post("/api/attendance/", body, format="json").status_code, 201)

    def test_event_scope_visibility_and_permissions(self):
        today = timezone.localdate()
        mk = lambda c, **kw: c.post("/api/calendar/", {"title": "x", "kind": "closure", "start_date": today.isoformat(),
                                                       "end_date": today.isoformat(), **kw}, format="json")
        sa, ra = self.api(self.sa), self.api(self.ra)
        self.assertEqual(mk(sa).status_code, 201)                                      # school-level (own school)
        self.assertEqual(mk(sa, nationwide=True).status_code, 403)                     # only super admin
        self.assertEqual(mk(self.api(self.t1)).status_code, 403)                       # teachers cannot create
        self.assertEqual(mk(sa, org=self.school2.pk).status_code, 403)                 # other school: out of scope
        self.assertEqual(mk(ra, org=self.reg.pk).status_code, 201)                     # region-wide
        names = lambda u: len(self.api(u).get("/api/calendar/").data)
        self.assertEqual((names(self.s1), names(self.t1)), (2, 2))                    # school + region events
        self.assertEqual((names(self.s4), names(self.t2)), (1, 1))                    # only the region event
        root = User.objects.create_superuser("root", password=PW)
        self.assertEqual(mk(self.api(root), nationwide=True).status_code, 201)
        self.assertEqual(names(self.s4), 2)
        # deletion rights
        ev_region = [e for e in self.api(self.sa).get("/api/calendar/").data if e["scope"] == "R"][0]
        self.assertFalse(ev_region["can_delete"])
        self.assertEqual(sa.delete(f"/api/calendar/{ev_region['id']}/").status_code, 403)
        own = [e for e in self.api(self.sa).get("/api/calendar/").data if e["scope"] == "A"][0]
        self.assertEqual(sa.delete(f"/api/calendar/{own['id']}/").status_code, 204)
        self.assertEqual(self.api(self.s1).delete(f"/api/calendar/{ev_region['id']}/").status_code, 403)

    def test_validation_and_seed_command(self):
        bad = {"title": "x", "kind": "event", "start_date": "2026-10-10", "end_date": "2026-10-01"}
        self.assertEqual(self.api(self.sa).post("/api/calendar/", bad, format="json").status_code, 400)
        call_command("seed_holidays", year=1405)
        self.assertEqual(CalendarEvent.objects.filter(org=None, kind="holiday").count(), 10)
        self.assertTrue(CalendarEvent.objects.filter(start_date="2026-03-21").exists())     # 1 Farvardin 1405
        call_command("seed_holidays", year=1405)                                            # idempotent
        self.assertEqual(CalendarEvent.objects.filter(org=None).count(), 10)


class AppealBase(Base):
    def graded_essay(self, grade="2"):
        qs = [{"kind": "essay", "text": "شرح دهید", "points": "5"}]
        eid = self.make_exam(qs)
        st = self.api(self.s1)
        start = st.post(f"/api/exams/{eid}/start/").data
        qid = start["questions"][0]["id"]
        st.put(f"/api/attempts/{start['attempt']}/answer/", {"question": qid, "text": "پاسخ من"}, format="json")
        st.post(f"/api/attempts/{start['attempt']}/submit/")
        t = self.api(self.t1)
        aid = Answer.objects.get(attempt_id=start["attempt"]).pk
        self.assertEqual(t.post(f"/api/answers/{aid}/grade/", {"points": grade}, format="json").status_code, 200)
        return start["attempt"], aid

    def file(self, kind, ref, user=None, text="نمره‌ام درست محاسبه نشده است"):
        return self.api(user or self.s1).post("/api/appeals/", {"kind": kind, "ref_id": ref, "text": text}, format="json")


class AppealTests(AppealBase):
    def test_accept_updates_grade_and_history(self):
        attempt, aid = self.graded_essay("2")
        kinds = {(e["kind"], e["ref_id"]) for e in self.api(self.s1).get("/api/appeals/eligible/").data}
        self.assertEqual(kinds, {("answer", aid), ("attempt", attempt)})
        r = self.file("answer", aid)
        self.assertEqual(r.status_code, 201)
        pk = r.data["id"]
        self.assertEqual(self.file("answer", aid).status_code, 409)                       # one appeal per item
        self.assertEqual(self.file("answer", aid, self.s2).status_code, 404)              # not their answer
        self.assertEqual(self.file("answer", aid, text="کم").status_code, 400)     # reason too short
        t = self.api(self.t1)
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "accept"}, format="json").status_code, 400)
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "accept", "new_points": "6"}, format="json").status_code, 400)
        self.assertEqual(self.api(self.t2).post(f"/api/appeals/{pk}/decide/", {"decision": "reject", "response": "xxxxxx"}, format="json").status_code, 404)
        r = t.post(f"/api/appeals/{pk}/decide/", {"decision": "accept", "new_points": "4", "response": "اصلاح شد"}, format="json")
        self.assertEqual((r.status_code, r.data["status"]), (200, "accepted"))
        self.assertEqual(float(Attempt.objects.get(pk=attempt).score), 4.0)
        hist = GradeChange.objects.filter(kind="answer", ref_id=aid).order_by("id")
        self.assertEqual([(None if h.old_points is None else float(h.old_points), float(h.new_points)) for h in hist], [(None, 2.0), (2.0, 4.0)])
        self.assertIn("اعتراض", hist.last().reason)
        d = self.api(self.s1).get(f"/api/appeals/{pk}/").data
        self.assertEqual([e["action"] for e in d["events"]], ["filed", "accept"])
        self.assertEqual(self.api(self.s2).get(f"/api/appeals/{pk}/").status_code, 404)
        self.assertEqual(self.api(self.t2).get("/api/appeals/").data, [])

    def test_reject_escalate_admin_decides(self):
        _, aid = self.graded_essay()
        pk = self.file("answer", aid).data["id"]
        t, st, sa = self.api(self.t1), self.api(self.s1), self.api(self.sa)
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "reject"}, format="json").status_code, 400)   # needs reason
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "reject", "response": "پاسخ ناقص بود"}, format="json").data["status"], "rejected")
        self.assertEqual(st.post(f"/api/appeals/{pk}/escalate/").data["status"], "escalated")
        self.assertEqual(st.post(f"/api/appeals/{pk}/escalate/").status_code, 409)
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "accept", "new_points": "5"}, format="json").status_code, 403)
        r = sa.post(f"/api/appeals/{pk}/decide/", {"decision": "reject", "response": "تأیید نمره معلم"}, format="json")
        self.assertEqual(r.data["status"], "rejected_final")
        self.assertEqual(st.post(f"/api/appeals/{pk}/withdraw/").status_code, 409)

    def test_whole_attempt_adjustment(self):
        attempt, _ = self.graded_essay("2")
        pk = self.file("attempt", attempt).data["id"]
        t = self.api(self.t1)
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "accept", "new_points": "9"}, format="json").status_code, 400)  # > total
        self.assertEqual(t.post(f"/api/appeals/{pk}/decide/", {"decision": "accept", "new_points": "3.5"}, format="json").status_code, 200)
        a = Attempt.objects.get(pk=attempt)
        self.assertEqual((float(a.score), float(a.adjustment)), (3.5, 1.5))

    def test_window_and_submission_appeal(self):
        attempt, aid = self.graded_essay()
        Answer.objects.filter(pk=aid).update(graded_at=timezone.now() - timedelta(days=10))
        self.assertEqual(self.file("answer", aid).status_code, 403)
        a = Assignment.objects.create(course=self.course, title="تمرین", due_at=timezone.now() + timedelta(days=1), is_published=True)
        sub = Submission.objects.create(assignment=a, student=self.s1, text="x", submitted_at=timezone.now(), points=10,
                                        graded_at=timezone.now())
        pk = self.file("submission", sub.pk).data["id"]
        r = self.api(self.t1).post(f"/api/appeals/{pk}/decide/", {"decision": "accept", "new_points": "15"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(float(Submission.objects.get(pk=sub.pk).points), 15.0)
        self.assertTrue(GradeChange.objects.filter(kind="submission", ref_id=sub.pk).exists())


class TransferTests(Base):
    def setUp(self):
        super().setUp()
        self.sa2 = User.objects.create_user("sa2", password=PW, role="school_admin", org=self.school2)
        self.dest_class = ClassRoom.objects.create(school=self.school2, academic_year=self.year, name="دهم ب۲",
                                                   grade=self.grade, homeroom_teacher=self.t2)
        self.dest_course = Course.objects.create(org=self.school2, title="ریاضی مقصد", teacher=self.t2, classroom=self.dest_class)
        ParentLink.objects.create(parent=self.parent, student=self.s1, is_verified=True)
        AttendanceRecord.objects.create(student=self.s1, classroom=self.classroom, date=timezone.localdate(),
                                        slot_key="c0p0", status="present")

    def create(self, user, student, **extra):
        body = {"student": student.pk, "to_school": self.school2.pk, "reason": "تغییر محل سکونت", **extra}
        return self.api(user).post("/api/transfers/", body, format="json")

    def test_cross_school_flow_keeps_history(self):
        r = self.create(self.sa, self.s1, to_classroom=self.dest_class.pk)
        self.assertEqual((r.status_code, r.data["status"]), (201, "pending_dest"))      # source admin released implicitly
        tid = r.data["id"]
        self.assertEqual(self.create(self.sa, self.s1).status_code, 409)               # one active transfer per student
        sa, sa2 = self.api(self.sa), self.api(self.sa2)
        self.assertEqual(sa2.post(f"/api/transfers/{tid}/release/", {"decision": "approve"}, format="json").status_code, 404)
        self.assertEqual(sa.post(f"/api/transfers/{tid}/accept/", {"decision": "approve"}, format="json").status_code, 404)
        self.assertEqual(sa2.post(f"/api/transfers/{tid}/complete/").status_code, 409)           # not accepted yet
        self.assertEqual(sa2.post(f"/api/transfers/{tid}/accept/", {"decision": "approve"}, format="json").data["status"], "ready")
        self.assertEqual(sa.post(f"/api/transfers/{tid}/complete/").status_code, 404)            # only destination executes
        done = sa2.post(f"/api/transfers/{tid}/complete/")
        self.assertEqual((done.status_code, done.data["status"]), (200, "completed"))
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.org_id, self.school2.pk)
        self.assertFalse(self.classroom.students.filter(pk=self.s1.pk).exists())
        self.assertTrue(self.dest_class.students.filter(pk=self.s1.pk).exists())
        self.assertFalse(Enrollment.objects.filter(student=self.s1, course=self.course).exists())
        self.assertTrue(Enrollment.objects.filter(student=self.s1, course=self.dest_course).exists())
        self.assertFalse(ParentLink.objects.get(student=self.s1).is_verified)                   # new school must re-verify
        self.assertTrue(AttendanceRecord.objects.filter(student=self.s1, classroom=self.classroom).exists())  # history kept
        self.assertEqual([e["action"] for e in done.data["events"]], ["requested", "released", "accepted", "completed"])

    def test_parent_request_and_rejection(self):
        r = self.api(self.parent).post("/api/transfers/", {"student": self.s1.pk, "to_school": self.school2.pk}, format="json")
        self.assertEqual((r.status_code, r.data["status"]), (201, "pending_source"))
        tid = r.data["id"]
        self.assertEqual(self.api(self.parent).post("/api/transfers/", {"student": self.s2.pk, "to_school": self.school2.pk}, format="json").status_code, 404)  # not their child
        self.assertEqual(len(self.api(self.parent).get("/api/transfers/").data), 1)
        self.assertEqual(self.api(self.sa).post(f"/api/transfers/{tid}/release/", {"decision": "approve"}, format="json").data["status"], "pending_dest")
        self.assertEqual(self.api(self.sa2).post(f"/api/transfers/{tid}/accept/", {"decision": "reject", "note": "ظرفیت ندارد"}, format="json").data["status"], "rejected")
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.org_id, self.school.pk)

    def test_class_move_inside_school(self):
        cls2 = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="دهم ج", grade=self.grade)
        course2 = Course.objects.create(org=self.school, title="فیزیک ج", teacher=self.t1, classroom=cls2)
        r = self.api(self.sa).post("/api/transfers/", {"student": self.s3.pk, "to_school": self.school.pk,
                                                        "to_classroom": cls2.pk}, format="json")
        self.assertEqual(r.data["status"], "ready")
        self.assertEqual(self.api(self.sa).post(f"/api/transfers/{r.data['id']}/complete/").status_code, 200)
        self.assertTrue(cls2.students.filter(pk=self.s3.pk).exists())
        self.assertFalse(self.classroom.students.filter(pk=self.s3.pk).exists())
        self.assertFalse(Enrollment.objects.filter(student=self.s3, course=self.course).exists())
        self.assertTrue(Enrollment.objects.filter(student=self.s3, course=course2).exists())
        self.assertEqual(self.s3.__class__.objects.get(pk=self.s3.pk).org_id, self.school.pk)
        self.assertEqual(self.api(self.sa).post("/api/transfers/", {"student": self.s3.pk, "to_school": self.school.pk}, format="json").status_code, 400)

    def test_scope_cancel_and_search(self):
        tid = self.create(self.sa, self.s1).data["id"]
        outsider = User.objects.create_user("sa9", password=PW, role="school_admin", org=self.school2.__class__.objects.create(
            name="C", kind="school", parent=self.reg))
        self.assertEqual(self.api(outsider).get("/api/transfers/").data, [])
        self.assertEqual(self.api(outsider).post(f"/api/transfers/{tid}/cancel/").status_code, 404)
        self.assertEqual(self.api(self.s1).get("/api/transfers/").data, [])
        self.assertEqual(self.api(self.s1).post("/api/transfers/", {"student": self.s1.pk, "to_school": self.school2.pk}, format="json").status_code, 403)
        self.assertEqual(self.api(self.sa).post(f"/api/transfers/{tid}/cancel/").data["status"], "cancelled")
        self.assertEqual(self.create(self.sa, self.s1).status_code, 201)                      # allowed again after cancel
        found = self.api(self.sa).get("/api/students/?q=s1").data
        self.assertEqual([s["username"] for s in found], ["s1"])
        self.assertEqual(self.api(self.sa2).get("/api/students/?q=s1").data, [])             # other school's admin cannot find
        self.assertEqual(self.api(self.s1).get("/api/students/?q=s1").status_code, 403)
        self.assertEqual(len(self.api(self.sa).get("/api/schools/search/?q=B").data), 1)
