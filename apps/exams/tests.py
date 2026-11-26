from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import OrgUnit, User
from apps.courses.models import Course, Enrollment
from apps.exams.models import Answer, Choice, Exam, Question
from apps.exams.tasks import auto_submit_expired


class ExamFlowTests(APITestCase):
    def setUp(self):
        cache.clear()
        prov = OrgUnit.objects.create(name="خراسان رضوی", kind="province")
        reg = OrgUnit.objects.create(name="منطقه ۱", kind="region", parent=prov)
        self.school = OrgUnit.objects.create(name="مدرسه الف", kind="school", parent=reg)
        self.other_school = OrgUnit.objects.create(name="مدرسه ب", kind="school", parent=reg)
        self.teacher = User.objects.create_user("t1", password="Str0ng-pass-1", role="teacher", org=self.school)
        self.s1 = User.objects.create_user("s1", password="Str0ng-pass-1", role="student", org=self.school)
        self.s2 = User.objects.create_user("s2", password="Str0ng-pass-1", role="student", org=self.school)
        self.outsider = User.objects.create_user("s3", password="Str0ng-pass-1", role="student", org=self.other_school)
        self.course = Course.objects.create(org=self.school, title="ریاضی", teacher=self.teacher)
        Enrollment.objects.create(course=self.course, student=self.s1)
        Enrollment.objects.create(course=self.course, student=self.s2)
        now = timezone.now()
        self.exam = Exam.objects.create(course=self.course, title="میان‌ترم", starts_at=now - timedelta(minutes=1),
                                        ends_at=now + timedelta(hours=1), duration_minutes=30,
                                        is_published=True, show_results=True)
        self.q = Question.objects.create(exam=self.exam, text="۲+۲؟")
        self.right = Choice.objects.create(question=self.q, text="۴", is_correct=True)
        self.wrong = Choice.objects.create(question=self.q, text="۵")

    def login(self, user):
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": user.username, "password": "Str0ng-pass-1"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return c

    def test_correct_answers_never_leak(self):
        r = self.login(self.s1).post(f"/api/exams/{self.exam.pk}/start/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("is_correct", r.content.decode())

    def test_not_enrolled_cannot_start(self):
        r = self.login(self.outsider).post(f"/api/exams/{self.exam.pk}/start/")
        self.assertEqual(r.status_code, 404)

    def test_start_is_idempotent_and_answer_upsert(self):
        c = self.login(self.s1)
        a1 = c.post(f"/api/exams/{self.exam.pk}/start/").data["attempt"]
        a2 = c.post(f"/api/exams/{self.exam.pk}/start/").data["attempt"]
        self.assertEqual(a1, a2)
        for choice in (self.wrong, self.right, self.right):      # change answer, then retry same one
            r = c.put(f"/api/attempts/{a1}/answer/", {"question": self.q.pk, "choice": choice.pk}, format="json")
            self.assertEqual(r.status_code, 204)
        self.assertEqual(Answer.objects.filter(attempt_id=a1).count(), 1)

    def test_submit_grades_once(self):
        c = self.login(self.s1)
        a = c.post(f"/api/exams/{self.exam.pk}/start/").data["attempt"]
        c.put(f"/api/attempts/{a}/answer/", {"question": self.q.pk, "choice": self.right.pk}, format="json")
        r1 = c.post(f"/api/attempts/{a}/submit/")
        r2 = c.post(f"/api/attempts/{a}/submit/")
        self.assertEqual((r1.data["score"], r1.data["total"]), (1, 1))
        self.assertEqual(r1.data, r2.data)
        late = c.put(f"/api/attempts/{a}/answer/", {"question": self.q.pk, "choice": self.wrong.pk}, format="json")
        self.assertEqual(late.status_code, 409)

    def test_cannot_touch_other_students_attempt(self):
        a = self.login(self.s1).post(f"/api/exams/{self.exam.pk}/start/").data["attempt"]
        r = self.login(self.s2).put(f"/api/attempts/{a}/answer/",
                                    {"question": self.q.pk, "choice": self.right.pk}, format="json")
        self.assertEqual(r.status_code, 404)

    def test_choice_from_other_question_rejected(self):
        c = self.login(self.s1)
        a = c.post(f"/api/exams/{self.exam.pk}/start/").data["attempt"]
        q2 = Question.objects.create(exam=self.exam, text="x")
        foreign = Choice.objects.create(question=q2, text="y")
        r = c.put(f"/api/attempts/{a}/answer/", {"question": self.q.pk, "choice": foreign.pk}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_expired_attempts_auto_submitted(self):
        c = self.login(self.s1)
        a = c.post(f"/api/exams/{self.exam.pk}/start/").data["attempt"]
        from apps.exams.models import Attempt
        Attempt.objects.filter(pk=a).update(deadline=timezone.now() - timedelta(minutes=5))
        self.assertEqual(auto_submit_expired(), 1)
        self.assertIsNotNone(Attempt.objects.get(pk=a).submitted_at)

    def test_org_scope_on_courses(self):
        r = self.login(self.outsider).get("/api/courses/")
        self.assertEqual(r.data["count"], 0)

    def test_login_lockout(self):
        c = APIClient()
        for _ in range(5):
            c.post("/api/auth/login/", {"username": "s1", "password": "bad"}, format="json")
        r = c.post("/api/auth/login/", {"username": "s1", "password": "Str0ng-pass-1"}, format="json")
        self.assertEqual(r.status_code, 401)
