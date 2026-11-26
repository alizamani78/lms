from apps.core.tests_phase2 import Base


class RosterIncludesLateEnrolmentsTests(Base):
    """A student enrolled in a course after the class list was made must appear in attendance."""

    def setUp(self):
        super().setUp()
        from apps.courses.models import Enrollment
        self.classroom.students.remove(self.s2)
        Enrollment.objects.get_or_create(course=self.course, student=self.s2)
        self.course.classroom = self.classroom
        self.course.save()

    def test_roster_lists_course_enrolled_student(self):
        r = self.api(self.t1).get(f"/api/attendance/classrooms/{self.classroom.pk}/students/")
        ids = [x["id"] for x in r.data]
        self.assertIn(self.s2.pk, ids)
