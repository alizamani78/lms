from apps.academics.models import Grade, Level, StudyField, Subject
from apps.core.tests_phase2 import Base


class CourseFilterTests(Base):
    def setUp(self):
        super().setUp()
        lvl = Level.objects.create(code="MS2", name="متوسطه دوم", order=3)
        self.g12 = Grade.objects.create(code="G12", name="دوازدهم", level=lvl)
        self.g10 = Grade.objects.create(code="G10", name="دهم", level=lvl)
        self.math = StudyField.objects.create(code="15000", name="ریاضی فیزیک", level=lvl)
        self.sci = StudyField.objects.create(code="15001", name="علوم تجربی", level=lvl)
        self.shared = Subject.objects.create(code="12200", name="ریاضی عمومی")
        self.shared.grades.set([self.g10, self.g12])
        self.shared.study_fields.set([self.math, self.sci])

    def test_subject_link_rules(self):
        self.assertEqual(self.shared.link_problems([self.g10], [self.math]), [])
        prim = Level.objects.create(code="PRI", name="ابتدایی", order=1)
        pg = Grade.objects.create(code="G1", name="اول", level=prim)
        self.assertTrue(self.shared.link_problems([pg], [self.math]))      # field of another level

    def test_catalogue_and_course_filters(self):
        self.assertEqual(len(self.api(self.sa).get(f"/api/subjects/?grade={self.g12.pk}").data), 1)
        self.assertEqual(len(self.api(self.sa).get(f"/api/subjects/?field={self.sci.pk}").data), 1)
        self.assertEqual(self.api(self.sa).get(f"/api/subjects/?grade={self.g12.pk}&field=99999").data, [])
        self.assertEqual(self.api(self.s1).get("/api/subjects/").status_code, 403)
        from apps.courses.models import Course
        Course.objects.create(org=self.school, title="ریاضی دهم", teacher=self.t1, classroom=self.classroom, subject=self.shared)
        self.assertEqual(self.api(self.sa).get(f"/api/courses/?grade={self.classroom.grade_id}").data["count"], 1)
        self.assertEqual(self.api(self.sa).get("/api/courses/?grade=99999").data["count"], 0)
