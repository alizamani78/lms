from datetime import date

from apps.core.tests_phase2 import Base
from apps.courses.models import Enrollment
from apps.evaluation.models import ClassGrade


class ClassGradeTests(Base):
    def setUp(self):
        super().setUp()
        Enrollment.objects.get_or_create(course=self.course, student=self.s1)
        Enrollment.objects.get_or_create(course=self.course, student=self.s2)
        self.classroom.students.add(self.s1, self.s2)

    def test_bulk_marks_by_the_course_teacher(self):
        r = self.api(self.t1).post("/api/class-grades/bulk/", {
            "course": self.course.pk, "title": "فعالیت", "date": date.today().isoformat(), "max_score": "20",
            "rows": [{"student": self.s1.pk, "score": "18"}, {"student": self.s2.pk, "score": "25"}]}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        self.assertEqual(r.data["saved"], 1)                       # 25 is above the maximum: refused row
        self.assertEqual(ClassGrade.objects.count(), 1)

    def test_only_the_teacher_of_the_course_marks_in_class(self):
        other = self.s3 if hasattr(self, "s3") else self.s1
        r = self.api(self.s1).post("/api/class-grades/bulk/", {"course": self.course.pk, "title": "x", "date": date.today().isoformat(),
                                                               "rows": [{"student": self.s2.pk, "score": "5"}]}, format="json")
        self.assertIn(r.status_code, (403,))

    def test_worksheet_ranks_students_in_the_class(self):
        ClassGrade.objects.create(student=self.s1, course=self.course, title="a", date=date.today(), score=15, max_score=20)
        ClassGrade.objects.create(student=self.s2, course=self.course, title="a", date=date.today(), score=19, max_score=20)
        r = self.api(self.t1).get(f"/api/classes/{self.classroom.pk}/worksheet/")
        self.assertEqual(r.status_code, 200, getattr(r, "data", ""))
        ranks = {row["student"]: row["rank"] for row in r.data["rows"]}
        self.assertEqual(ranks[self.s2.pk], 1)
        self.assertEqual(ranks[self.s1.pk], 2)


class GraceAndPeriodTests(Base):
    def setUp(self):
        super().setUp()
        from apps.courses.models import Enrollment
        Enrollment.objects.get_or_create(course=self.course, student=self.s1)
        self.classroom.students.add(self.s1)
        self.course.classroom = self.classroom
        self.course.save()

    def test_grace_is_added_to_the_course_mark_and_capped(self):
        ClassGrade.objects.create(student=self.s1, course=self.course, title="a", date=date.today(), score=18, max_score=20, period="monthly")
        r = self.api(self.t1).post("/api/class-grades/grace/", {"course": self.course.pk, "students": [self.s1.pk], "points": "5", "reason": "تلاش"}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", ""))
        ws = self.api(self.t1).get(f"/api/classes/{self.classroom.pk}/worksheet/").data
        self.assertEqual(ws["rows"][0]["courses"][str(self.course.pk)], 95.0)

    def test_worksheet_csv_export(self):
        r = self.api(self.t1).get(f"/api/classes/{self.classroom.pk}/worksheet/?format=csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r["Content-Type"])
