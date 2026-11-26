from datetime import timedelta

from django.utils import timezone

from apps.accounts.models import OrgUnit
from apps.coursework.models import Assignment, Submission
from apps.core.tests_phase2 import Base
from apps.exams.models import Attempt, Exam
from apps.evaluation.logic import band_of, policy_for
from apps.evaluation.models import EvaluationPolicy


class EvaluationTests(Base):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        exam = Exam.objects.create(course=self.course, title="e", starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1),
                                   is_published=True)
        Attempt.objects.create(exam=exam, student=self.s1, deadline=now, submitted_at=now, score=8, total=10)      # 80%
        Attempt.objects.create(exam=exam, student=self.s2, deadline=now, submitted_at=now, score=3, total=10)      # 30%
        a = Assignment.objects.create(course=self.course, title="t", due_at=now + timedelta(days=1), max_points=20, is_published=True)
        Submission.objects.create(assignment=a, student=self.s1, text="x", submitted_at=now, points=18)            # 90%

    def test_bands_and_defaults(self):
        self.assertEqual(band_of(90, [{"min": 85, "label": "عالی"}, {"min": 0, "label": "ضعیف"}]), "عالی")
        self.assertEqual(policy_for(self.school)[1], 50.0)

    def test_school_policy_overrides_region_and_is_scoped(self):
        EvaluationPolicy.objects.create(org=self.reg, pass_percent=60)
        self.assertEqual(policy_for(self.school)[1], 60.0)
        EvaluationPolicy.objects.create(org=self.school, pass_percent=40)
        self.assertEqual(policy_for(self.school)[1], 40.0)
        self.assertEqual(self.api(self.sa).put("/api/evaluation-policy/", {"org": self.reg.pk, "pass_percent": 70, "bands": []}, format="json").status_code, 403)
        r = self.api(self.sa).put("/api/evaluation-policy/", {"org": self.school.pk, "pass_percent": 45,
                                                              "bands": [{"label": "خوب", "min": 60}, {"label": "ضعیف", "min": 0}]}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_school_evaluation_report_and_student_view(self):
        r = self.api(self.sa).get(f"/api/reports/run/evaluation_students/?org={self.school.pk}")
        rows = {x["student"]: x for x in r.data["rows"]}
        s1 = self.s1.get_full_name() or self.s1.username
        self.assertEqual(rows[s1]["status"], "قبول")
        self.assertEqual(rows[s1]["band"], "عالی")                      # (80 + 90) / 2 = 85
        self.assertEqual(self.api(self.s1).get("/api/me/evaluation/").data["average"], 85.0)
        self.assertEqual(self.api(self.t1).get(f"/api/reports/run/evaluation_students/?org={self.school.pk}").status_code, 403)

    def test_district_report_for_region_admin(self):
        r = self.api(self.ra).get("/api/reports/run/evaluation_schools/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(x["school"] == self.school.name for x in r.data["rows"]))


class FinalGradeFormulaTests(Base):
    """نمره نهایی = (مستمر × ضریب مستمر + پایانی × ضریب پایانی) ÷ (ضریب مستمر + ضریب پایانی)"""

    def setUp(self):
        super().setUp()
        from apps.exams.models import Exam, Attempt
        now = timezone.now()
        cont = Exam.objects.create(course=self.course, title="میان‌ترم", starts_at=now - timedelta(hours=1),
                                   ends_at=now + timedelta(hours=1), is_published=True, assessment="continuous")
        fin = Exam.objects.create(course=self.course, title="پایان ترم", starts_at=now - timedelta(hours=1),
                                  ends_at=now + timedelta(hours=1), is_published=True, assessment="final")
        Attempt.objects.create(exam=cont, student=self.s1, deadline=now, submitted_at=now, score=12, total=20)   # 60
        Attempt.objects.create(exam=fin, student=self.s1, deadline=now, submitted_at=now, score=16, total=20)    # 80
        EvaluationPolicy.objects.create(org=None, pass_percent=50, continuous_weight=1, final_weight=3)

    def test_weighted_formula(self):
        from apps.evaluation.logic import course_percentages, courses_with_final, evaluate_students, policy_for, weights_for
        pct = course_percentages([self.school.pk], student_ids=[self.s1.pk])
        rows = evaluate_students(self.school, pct, policy_for(self.school)[1], policy_for(self.school)[2],
                                 finals=courses_with_final([self.school.pk]), weights=weights_for(self.school))
        detail = rows[0]["course_detail"][0]
        # (60×1 + 80×3) / (1+3) = 75
        self.assertEqual(detail["average"], 75.0)
        self.assertEqual(detail["continuous"], 60.0)
        self.assertEqual(detail["final"], 80.0)

    def test_pending_when_final_has_no_marks_yet(self):
        from apps.evaluation.logic import course_percentages, courses_with_final, evaluate_students
        from apps.exams.models import Attempt, Exam
        cont = Exam.objects.get(assessment="continuous")
        Attempt.objects.create(exam=cont, student=self.s2, deadline=timezone.now(), submitted_at=timezone.now(), score=18, total=20)
        Attempt.objects.filter(student=self.s2, exam__assessment="final").delete()
        pct = course_percentages([self.school.pk], student_ids=[self.s2.pk])
        rows = evaluate_students(self.school, pct, 50, [{"label": "x", "min": 0}],
                                 finals=courses_with_final([self.school.pk]))
        self.assertEqual(rows, [])                                    # final exists but has no marks: grade pending
