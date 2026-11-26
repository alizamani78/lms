from datetime import time

from apps.academics.models import ClassRoom
from apps.accounts.models import SchoolMembership, User
from apps.core.tests_phase2 import Base, PW
from apps.timetable.models import BellPeriod, ScheduleEntry


class FieldFixesTests(Base):
    def test_staff_can_read_pickers_inside_scope(self):
        r = self.api(self.t1).get(f"/api/manage/options/?model=accounts.User&role=student&school={self.school.pk}")
        self.assertEqual(r.status_code, 200)

    def test_principal_can_assign_deputies_to_his_school(self):
        from apps.sysadmin.resources import USER_ROLE_RULES
        self.assertIn("school_deputy_edu", USER_ROLE_RULES["roles"])
        self.assertEqual(USER_ROLE_RULES["roles"]["school_deputy_edu"]["required"], ["org"])

    def test_student_timetable_follows_enrolled_course(self):
        from apps.courses.models import Enrollment
        from datetime import date
        classroom = ClassRoom.objects.create(school=self.school, academic_year=self.year, name="کلاس فیلد", code="F1", grade=self.grade)
        period = BellPeriod.objects.create(school=self.school, number=1, start_time=time(7, 30), end_time=time(8, 15))
        self.course.classroom = classroom
        self.course.save()
        ScheduleEntry.objects.create(classroom=classroom, period=period, course=self.course, weekday=0, room="۱۰۱")
        Enrollment.objects.get_or_create(course=self.course, student=self.s1)
        r = self.api(self.s1).get("/api/schedule/")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.data["entries"]), 1)
