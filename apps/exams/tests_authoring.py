from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import OrgUnit, User
from apps.courses.models import Course, Enrollment
from apps.exams.models import Attempt, Exam

PW = "Str0ng-pass-1"


def payload(**over):
    now = timezone.now()
    d = {"title": "کوییز", "starts_at": (now - timedelta(minutes=1)).isoformat(),
         "ends_at": (now + timedelta(hours=1)).isoformat(), "duration_minutes": 20, "show_results": True,
         "questions": [{"text": "۲+۲؟", "choices": [{"text": "۴", "is_correct": True}, {"text": "۵"}]},
                       {"text": "۳×۳؟", "choices": [{"text": "۹", "is_correct": True}, {"text": "۶"}]}]}
    d.update(over)
    return d


class AuthoringTests(APITestCase):
    def setUp(self):
        cache.clear()
        prov = OrgUnit.objects.create(name="P", kind="province")
        reg = OrgUnit.objects.create(name="R", kind="region", parent=prov)
        self.school = OrgUnit.objects.create(name="A", kind="school", parent=reg)
        self.school2 = OrgUnit.objects.create(name="B", kind="school", parent=reg)
        self.t1 = User.objects.create_user("t1", password=PW, role="teacher", org=self.school)
        self.t2 = User.objects.create_user("t2", password=PW, role="teacher", org=self.school2)
        self.st = User.objects.create_user("s1", password=PW, role="student", org=self.school)
        self.course = Course.objects.create(org=self.school, title="ریاضی", teacher=self.t1)
        Enrollment.objects.create(course=self.course, student=self.st)

    def client_for(self, user):
        c = APIClient()
        r = c.post("/api/auth/login/", {"username": user.username, "password": PW}, format="json")
        c.credentials(HTTP_AUTHORIZATION="Bearer " + r.data["access"])
        return c

    def create(self, c=None, **over):
        c = c or self.client_for(self.t1)
        return c.post(f"/api/courses/{self.course.pk}/exams/", payload(**over), format="json")

    def test_teacher_creates_and_publishes_then_student_takes(self):
        t = self.client_for(self.t1)
        eid = self.create(t).data["id"]
        self.assertEqual(self.client_for(self.st).post(f"/api/exams/{eid}/start/").status_code, 404)  # unpublished
        self.assertEqual(t.post(f"/api/exams/{eid}/publish/", {"published": True}, format="json").status_code, 200)
        r = self.client_for(self.st).post(f"/api/exams/{eid}/start/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["questions"]), 2)
        self.assertNotIn("is_correct", r.content.decode())

    def test_validation(self):
        bad_two_correct = payload(questions=[{"text": "q", "choices": [
            {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True}]}])
        self.assertEqual(self.client_for(self.t1).post(f"/api/courses/{self.course.pk}/exams/",
                                                       bad_two_correct, format="json").status_code, 400)
        now = timezone.now()
        r = self.create(starts_at=now.isoformat(), ends_at=(now - timedelta(hours=1)).isoformat())
        self.assertEqual(r.status_code, 400)

    def test_other_teacher_and_student_blocked(self):
        eid = self.create().data["id"]
        self.assertEqual(self.client_for(self.t2).get(f"/api/exams/{eid}/manage/").status_code, 404)
        self.assertEqual(self.client_for(self.t2).post(f"/api/courses/{self.course.pk}/exams/",
                                                       payload(), format="json").status_code, 404)
        self.assertEqual(self.client_for(self.st).get(f"/api/exams/{eid}/manage/").status_code, 403)

    def test_cannot_publish_empty_or_edit_after_attempt(self):
        t = self.client_for(self.t1)
        empty = self.create(t, questions=[]).data["id"]
        self.assertEqual(t.post(f"/api/exams/{empty}/publish/", {"published": True}, format="json").status_code, 400)
        eid = self.create(t).data["id"]
        exam = Exam.objects.get(pk=eid)
        Attempt.objects.create(exam=exam, student=self.st, deadline=timezone.now() + timedelta(minutes=5))
        r = t.put(f"/api/exams/{eid}/questions/", {"questions": payload()["questions"]}, format="json")
        self.assertEqual(r.status_code, 409)
        r = t.patch(f"/api/exams/{eid}/manage/", {"duration_minutes": 99}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(t.patch(f"/api/exams/{eid}/manage/", {"title": "جدید"}, format="json").status_code, 200)
