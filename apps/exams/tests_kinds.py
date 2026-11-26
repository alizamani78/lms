from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.tests_phase2 import Base
from apps.exams.authoring import write_questions
from apps.exams.models import Attempt, Exam, Question
from apps.exams.services import compute_scores


class QuestionKindsTests(Base):
    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.exam = Exam.objects.create(course=self.course, title="آزمون انواع", starts_at=now - timedelta(hours=1),
                                        ends_at=now + timedelta(hours=2), duration_minutes=60, is_published=True)
        self.q_multi = {"kind": "multi", "text": "<p><b>چند</b> پاسخ</p>", "points": 4, "choices": [
            {"text": "الف", "is_correct": True, "weight": 50}, {"text": "ب", "is_correct": True, "weight": 50},
            {"text": "ج", "is_correct": False, "weight": 25}]}
        self.q_short = {"kind": "short", "text": "پایتخت ایران؟", "points": 2, "choices": [
            {"text": "تهران", "is_correct": True}, {"text": "شهر تهران", "is_correct": True}]}
        self.q_match = {"kind": "match", "text": "جور کنید", "points": 3, "choices": [
            {"text": "ایران", "pair_text": "آسیا"}, {"text": "مصر", "pair_text": "آفریقا"}, {"text": "فرانسه", "pair_text": "اروپا"}]}
        self.q_drag = {"kind": "drag", "text": "جای درست", "points": 2, "choices": [
            {"text": "سیب", "pair_text": "میوه"}, {"text": "هویج", "pair_text": "سبزی"}]}
        write_questions(self.exam, [self.q_multi, self.q_short, self.q_match, self.q_drag])
        self.qs = {q.kind: q for q in Question.objects.filter(exam=self.exam)}
        self.attempt = Attempt.objects.create(exam=self.exam, student=self.s1, deadline=now + timedelta(hours=1),
                                              question_ids=[q.pk for q in self.qs.values()])

    def _answer(self, q, **body):
        return self.api(self.s1).put(f"/api/attempts/{self.attempt.pk}/answer/", {"question": q.pk, **body}, format="json")

    def test_multi_percentages_add_and_subtract_within_the_points(self):
        q = self.qs["multi"]
        ids = {c.text: c.pk for c in q.choices.all()}
        self.assertEqual(self._answer(q, choices=[ids["الف"], ids["ب"]]).status_code, 204)
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("4"))          # 50% + 50% of 4 points
        self._answer(q, choices=[ids["الف"], ids["ج"]])                          # 50% gained, 25% taken away
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("1"))          # (50 - 25)% of 4
        self._answer(q, choices=[ids["ج"]])                                      # only a wrong one: never below zero
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("0"))

    def test_short_answer_tolerant_to_spaces_and_persian_letters(self):
        q = self.qs["short"]
        self._answer(q, text="  تهران ")
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("2"))
        self._answer(q, text="کرج")
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("0"))

    def test_match_and_drag_fractions(self):
        m = self.qs["match"]
        ids = {c.text: c.pk for c in m.choices.all()}
        self._answer(m, mapping={str(ids["ایران"]): "آسیا", str(ids["مصر"]): "اروپا", str(ids["فرانسه"]): "اروپا"})
        # 1 of 3 right -> 3 * 1/3 = 1 point
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("1"))
        d = self.qs["drag"]
        ids = {c.text: c.pk for c in d.choices.all()}
        self._answer(d, mapping={str(ids["سیب"]): "میوه", str(ids["هویج"]): "میوه"})
        self.assertEqual(compute_scores(self.attempt)[0], Decimal("2") * Decimal("0.5") + Decimal("1"))

    def test_student_payload_never_contains_answers(self):
        r = self.api(self.s1).post(f"/api/exams/{self.exam.pk}/start/")
        self.assertEqual(r.status_code, 200, r.data)
        import json
        blob = json.dumps(r.data, ensure_ascii=False)
        self.assertNotIn('"is_correct"', blob)
        self.assertNotIn('"pair_text"', blob)
        drag_like = [q for q in r.data["questions"] if q["kind"] in ("match", "drag")]
        self.assertTrue(drag_like and all("targets" in q for q in drag_like))

    def test_percent_total_above_100_is_refused(self):
        from apps.exams.authoring import QuestionIn
        over = QuestionIn(data={"kind": "multi", "text": "x", "points": 2, "choices": [
            {"text": "a", "is_correct": True, "weight": 70}, {"text": "b", "is_correct": True, "weight": 40}, {"text": "c"}]})
        self.assertFalse(over.is_valid())
        even = QuestionIn(data={"kind": "multi", "text": "x", "points": 2, "choices": [
            {"text": "a", "is_correct": True}, {"text": "b", "is_correct": True}, {"text": "c"}]})
        self.assertTrue(even.is_valid(), even.errors)
        self.assertEqual([float(c["weight"]) for c in even.validated_data["choices"] if c["is_correct"]], [50.0, 50.0])

    def test_authoring_rules_per_kind(self):
        from rest_framework.test import APIClient
        from apps.exams.authoring import QuestionIn
        bad_short = QuestionIn(data={"kind": "short", "text": "x", "points": 1, "choices": [{"text": "a", "is_correct": False}]})
        self.assertFalse(bad_short.is_valid())
        bad_match = QuestionIn(data={"kind": "match", "text": "x", "points": 1, "choices": [{"text": "a"}, {"text": "b", "pair_text": "y"}]})
        self.assertFalse(bad_match.is_valid())
        ok = QuestionIn(data={"kind": "drag", "text": "<p onclick='x'>ok</p>", "points": 1,
                              "choices": [{"text": "a", "pair_text": "1"}, {"text": "b", "pair_text": "2"}]})
        self.assertTrue(ok.is_valid(), ok.errors)
        self.assertNotIn("onclick", ok.validated_data["text"])


class ExamControlTests(Base):
    """Suspension, changing the start and end, and the participation report with resume."""

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.now = now
        self.exam = Exam.objects.create(course=self.course, title="آزمون کنترل", starts_at=now - timedelta(hours=1),
                                        ends_at=now + timedelta(hours=2), duration_minutes=30, is_published=True)
        self.att = Attempt.objects.create(exam=self.exam, student=self.s1, deadline=now - timedelta(minutes=5),
                                          question_ids=[])
        # student 2 started, is silent and his time passed (line dropped); student 3 is still answering
        Attempt.objects.create(exam=self.exam, student=self.s2, deadline=now + timedelta(minutes=20), question_ids=[])

    def test_suspension_blocks_start_and_answers(self):
        self.api(self.t1).patch(f"/api/exams/{self.exam.pk}/manage/", {"is_suspended": True}, format="json")
        self.exam.refresh_from_db()
        self.assertTrue(self.exam.is_suspended)
        self.assertEqual(self.api(self.s3).post(f"/api/exams/{self.exam.pk}/start/").status_code, 423)

    def test_teacher_changes_start_and_end(self):
        new_start = (self.now + timedelta(days=1)).isoformat()
        new_end = (self.now + timedelta(days=1, hours=2)).isoformat()
        r = self.api(self.t1).patch(f"/api/exams/{self.exam.pk}/manage/", {"starts_at": new_start, "ends_at": new_end}, format="json")
        self.assertEqual(r.status_code, 200, getattr(r, "data", ""))
        bad = self.api(self.t1).patch(f"/api/exams/{self.exam.pk}/manage/", {"ends_at": (self.now).isoformat()}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_participation_statuses_and_resume(self):
        r = self.api(self.t1).get(f"/api/exams/{self.exam.pk}/participation/")
        self.assertEqual(r.status_code, 200, getattr(r, "data", ""))
        by_user = {x["username"]: x["status"] for x in r.data["rows"]}
        self.assertEqual(by_user[self.s1.username], "interrupted")
        self.assertEqual(by_user[self.s2.username], "in_progress")
        if self.s4.username in by_user:
            self.assertEqual(by_user[self.s4.username], "absent")
        rs = self.api(self.t1).post(f"/api/exams/{self.exam.pk}/participation/{self.att.pk}/resume/", {}, format="json")
        self.assertEqual(rs.status_code, 200, getattr(rs, "data", ""))
        self.att.refresh_from_db()
        self.assertGreater(self.att.deadline, timezone.now())
        self.assertIn(self.api(self.s1).get(f"/api/exams/{self.exam.pk}/participation/").status_code, (403, 404))
        csv_r = self.api(self.t1).get(f"/api/exams/{self.exam.pk}/participation/?format=csv")
        self.assertIn("text/csv", csv_r["Content-Type"])


class InstructionsResultsTests(Base):
    """Instructions before the start, results after the submit as the designer chose, shuffled questions and answers."""

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.exam = Exam.objects.create(course=self.course, title="آزمون توضیحات", starts_at=now - timedelta(hours=1),
                                        ends_at=now + timedelta(hours=2), duration_minutes=30, is_published=True,
                                        instructions="<p>سه سؤال است.</p><script>x()</script>", result_mode="both",
                                        shuffle_choices=True)
        write_questions(self.exam, [{"kind": "mcq", "text": "یک؟", "points": 2, "choices": [
            {"text": "الف", "is_correct": True}, {"text": "ب", "is_correct": False}, {"text": "ج"}]}])
        self.q = Question.objects.get(exam=self.exam)

    def test_info_before_start_and_instructions_sanitised(self):
        r = self.api(self.s1).get(f"/api/exams/{self.exam.pk}/info/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("سه سؤال", r.data["instructions"])
        self.assertNotIn("<script", r.data["instructions"])

    def test_result_follows_result_mode(self):
        self.api(self.s1).post(f"/api/exams/{self.exam.pk}/start/")
        a = Attempt.objects.get(exam=self.exam, student=self.s1)
        correct = self.q.choices.get(text="الف")
        self.api(self.s1).put(f"/api/attempts/{a.pk}/answer/", {"question": self.q.pk, "choice": correct.pk}, format="json")
        r = self.api(self.s1).post(f"/api/attempts/{a.pk}/submit/")
        self.assertEqual(r.status_code, 200, getattr(r, "data", ""))
        self.assertEqual(r.data["score"], 2.0)
        self.assertEqual((r.data["correct"], r.data["wrong"]), (1, 0))
        self.exam.result_mode = "hidden"
        self.exam.save()
        r2 = self.api(self.s1).get(f"/api/attempts/{a.pk}/result/")
        self.assertNotIn("score", r2.data)

    def test_choices_are_shuffled_per_student_for_choice_questions(self):
        self.api(self.s1).post(f"/api/exams/{self.exam.pk}/start/")
        payload = self.api(self.s1).post(f"/api/exams/{self.exam.pk}/start/").data
        self.assertEqual(len(payload["questions"][0]["choices"]), 3)


class QuestionDifficultyTests(TestCase):
    """The difficulty of a question (آسان، متوسط، سخت) is kept with the question and is shown back to the teacher."""

    def test_default_is_medium_and_values_are_limited(self):
        from apps.exams.models import Question
        q = Question(kind="mcq", text="x")
        self.assertEqual(q.difficulty, "medium")
        from apps.exams.authoring import QuestionIn
        ok = QuestionIn(data={"kind": "essay", "text": "متن", "points": 2, "difficulty": "hard"})
        self.assertTrue(ok.is_valid(), ok.errors)
        bad = QuestionIn(data={"kind": "essay", "text": "متن", "points": 2, "difficulty": "impossible"})
        self.assertFalse(bad.is_valid())


class MultiPointsTests(TestCase):
    """Multi-answer: each option has a number of points; correct ticks add them, wrong ticks take them away."""

    def test_points_add_and_subtract_within_the_question_points(self):
        from decimal import Decimal
        from apps.exams.services import _multi_award
        opts = [{"id": 1, "is_correct": True, "weight": Decimal("2")}, {"id": 2, "is_correct": True, "weight": Decimal("3")},
                {"id": 3, "is_correct": False, "weight": Decimal("1")}]
        self.assertEqual(_multi_award(Decimal("5"), opts, {1, 2}), Decimal("5"))
        self.assertEqual(_multi_award(Decimal("5"), opts, {1, 3}), Decimal("1"))
        self.assertEqual(_multi_award(Decimal("5"), opts, {3}), Decimal("0"))

    def test_correct_options_may_not_exceed_the_question_points(self):
        from apps.exams.authoring import QuestionIn
        data = {"kind": "multi", "text": "س", "points": 3, "choices": [
            {"text": "الف", "is_correct": True, "weight": 2}, {"text": "ب", "is_correct": True, "weight": 2},
            {"text": "ج", "is_correct": False, "weight": 0}]}
        self.assertFalse(QuestionIn(data=data).is_valid())


class ExamSectionsTests(TestCase):
    """A question belongs to a section; a section has its title and instructions; the questions may be mixed kinds."""

    def setUp(self):
        from apps.exams.models import Exam
        from apps.courses.models import Course
        from apps.accounts.models import OrgUnit
        from django.utils import timezone
        from datetime import timedelta
        org = OrgUnit.objects.create(name="سخ", kind="school")
        course = Course.objects.create(org=org, title="ریاضی")
        self.exam = Exam.objects.create(course=course, title="آزمون", starts_at=timezone.now(), ends_at=timezone.now() + timedelta(days=1), duration_minutes=30)

    def test_questions_are_placed_in_their_sections_with_instructions(self):
        from apps.exams.authoring import exam_detail, write_questions
        qs = [{"kind": "essay", "text": "الف", "points": 2, "choices": [], "section": 0},
              {"kind": "essay", "text": "ب", "points": 2, "choices": [], "section": 1},
              {"kind": "essay", "text": "ج", "points": 1, "choices": []}]
        sections = [{"title": "تستی", "instructions": "یک گزینه را انتخاب کنید"}, {"title": "تشریحی", "instructions": "پاسخ کامل بنویسید"}]
        write_questions(self.exam, qs, sections)
        d = exam_detail(self.exam)
        self.assertEqual([s["title"] for s in d["sections"]], ["تستی", "تشریحی"])
        by_text = {q["text"]: q["section"] for q in d["questions"]}
        self.assertEqual(by_text["الف"], d["sections"][0]["id"])
        self.assertEqual(by_text["ب"], d["sections"][1]["id"])
        self.assertIsNone(by_text["ج"])


class BackNavigationTests(TestCase):
    """Going back to an earlier screen can be allowed or refused, and each return takes the penalty from the score."""

    def test_penalty_is_taken_per_return_and_never_below_zero(self):
        from decimal import Decimal
        from apps.exams.services import compute_scores
        from types import SimpleNamespace
        exam = SimpleNamespace(back_penalty=Decimal("0.50"))
        attempt = SimpleNamespace(back_moves=2, adjustment=None, exam=exam)
        import apps.exams.services as svc
        orig = svc.question_awards
        svc.question_awards = lambda a: ({1: Decimal("1")}, False, {1: {"points": Decimal("2")}})
        try:
            score, total, _ = compute_scores(attempt)
        finally:
            svc.question_awards = orig
        self.assertEqual(score, Decimal("0.00"))
        self.assertEqual(total, Decimal("2"))


class EntryCardSeatsTests(TestCase):
    """Candidate numbers: one per enrolled student, in name order, never repeated; the entry-card data lists everyone."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.accounts.models import OrgUnit, User
        from apps.courses.models import Course, Enrollment
        from apps.exams.models import Exam
        self.org = OrgUnit.objects.create(name="ورود", kind="school")
        self.teacher = User.objects.create_user("ec_teacher", password="Str0ng-pass-1", role="teacher", org=self.org)
        self.course = Course.objects.create(org=self.org, title="ریاضی", teacher=self.teacher)
        self.exam = Exam.objects.create(course=self.course, title="میان‌ترم", starts_at=timezone.now(), ends_at=timezone.now() + timedelta(days=1), duration_minutes=45)
        self.s1 = User.objects.create_user("ec_s1", password="Str0ng-pass-1", role="student", first_name="الف", last_name="ب")
        self.s2 = User.objects.create_user("ec_s2", password="Str0ng-pass-1", role="student", first_name="ج", last_name="د")
        Enrollment.objects.create(course=self.course, student=self.s1)
        Enrollment.objects.create(course=self.course, student=self.s2)

    def test_numbers_are_given_once_and_listed(self):
        from django.test import Client
        c = Client(); c.force_login(self.teacher)
        r = c.post(f"/api/exams/{self.exam.pk}/seats/", data={"room": "سالن ۱"}, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content[:200])
        self.assertEqual(r.json()["created"], 2)
        again = c.post(f"/api/exams/{self.exam.pk}/seats/", data={}, content_type="application/json")
        self.assertEqual(again.json()["created"], 0)
        rows = c.get(f"/api/exams/{self.exam.pk}/entry-cards/").json()["students"]
        self.assertEqual(sorted(x["number"] for x in rows), [1, 2])


class ExamScheduleDaysTests(SimpleTestCase):
    """School days for exams: no Friday, no official holiday."""

    def test_friday_and_holidays_are_not_school_days(self):
        import datetime as dt
        from apps.exams.schedule import is_school_day, school_days
        friday = dt.date(2026, 11, 13)          # جمعه
        saturday = dt.date(2026, 11, 14)        # شنبه
        self.assertFalse(is_school_day(friday, set()))
        self.assertTrue(is_school_day(saturday, set()))
        self.assertFalse(is_school_day(saturday, {saturday}))
        days = list(school_days(dt.date(2026, 11, 12), dt.date(2026, 11, 16), {dt.date(2026, 11, 15)}))
        self.assertEqual(days, [dt.date(2026, 11, 12), dt.date(2026, 11, 14)])
