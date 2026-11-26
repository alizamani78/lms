from datetime import date, timedelta

from django.utils import timezone

from apps.attendance.models import AttendanceRecord, TeacherAttendance
from apps.coursework.models import Assignment, Submission
from apps.core.tests_phase2 import Base


class AttendanceAssignmentReportTests(Base):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        AttendanceRecord.objects.create(student=self.s1, classroom=self.classroom, date=today, slot_key="c0p0", status="absent")
        AttendanceRecord.objects.create(student=self.s2, classroom=self.classroom, date=today, slot_key="c0p0", status="present")
        self.assn = Assignment.objects.create(course=self.course, title="تمرین", due_at=timezone.now() + timedelta(days=1),
                                              is_published=True)
        Submission.objects.create(assignment=self.assn, student=self.s1, text="x", submitted_at=timezone.now(), points=15)

    def test_student_absence_and_class_rate(self):
        r = self.api(self.sa).get(f"/api/reports/run/absence_students/?org={self.school.pk}")
        rows = {x["student"]: x for x in r.data["rows"]}
        self.assertEqual(rows[self.s1.username if not self.s1.get_full_name() else self.s1.get_full_name()]["absent"], 1)
        c = self.api(self.sa).get(f"/api/reports/run/attendance_classes/?org={self.school.pk}")
        self.assertEqual(c.data["rows"][0]["rate"], 50.0)

    def test_teacher_attendance_entry_and_report(self):
        today = timezone.localdate().isoformat()
        r = self.api(self.sa).post("/api/teacher-attendance/", {"school": self.school.pk, "date": today,
                                                              "records": [{"teacher": self.t1.pk, "status": "absent"}]}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(TeacherAttendance.objects.get(teacher=self.t1).status, "absent")
        rep = self.api(self.sa).get(f"/api/reports/run/teacher_attendance/?org={self.school.pk}")
        self.assertEqual(rep.data["totals"]["absent"], 1)
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        self.assertEqual(self.api(self.sa).post("/api/teacher-attendance/", {"school": self.school.pk, "date": future,
                                                                             "records": []}, format="json").status_code, 400)
        other = self.api(self.t1).post("/api/teacher-attendance/", {"school": self.school.pk, "date": today, "records": []}, format="json")
        self.assertEqual(other.status_code, 403)

    def test_assignment_report_school_and_teacher(self):
        r = self.api(self.sa).get(f"/api/reports/run/assignments_school/?org={self.school.pk}")
        row = r.data["rows"][0]
        self.assertEqual((row["submitted"], row["expected"], row["graded"]), (1, 3, 1))
        mine = self.api(self.t1).get("/api/reports/run/my_assignments/")
        self.assertEqual(mine.status_code, 200)
        self.assertEqual(mine.data["totals"]["assignments"], 1)
        self.assertEqual(self.api(self.t2).get("/api/reports/run/my_assignments/").data["totals"]["assignments"], 0)
        self.assertEqual(self.api(self.t1).get(f"/api/reports/run/assignments_school/?org={self.school.pk}").status_code, 403)
