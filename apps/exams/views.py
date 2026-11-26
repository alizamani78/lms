from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import kartabl_filter
from apps.core.scope import org_scope_q

from .models import Answer, Attempt, Choice, Exam, Question, QuestionKind
import random

from .services import GRACE_SECONDS, draw_questions, submit_attempt

IsStudent = role_required(User.Role.STUDENT)


class ExamThrottled(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


class ExamListView(ExamThrottled):
    """Students: published exams of their courses. Staff: exams in their scope."""

    def get(self, request):
        u = request.user
        qs = Exam.objects.all()
        if u.role == User.Role.STUDENT:
            qs = qs.filter(is_published=True, course__enrollments__student=u)
        elif u.role == User.Role.TEACHER:
            qs = qs.filter(course__teacher=u)
        elif u.role in User.ADMIN_ROLES or u.role == User.Role.EXPERT:
            qs = qs.filter(org_scope_q(u, "course__org"))
        else:
            qs = qs.none()
        qs = qs.filter(**kartabl_filter(request, "course__org_id"))
        rows = qs.order_by("-starts_at").values("id", "title", "course_id", "starts_at", "ends_at",
                                                 "duration_minutes", "is_published", "is_suspended")[:200]
        return Response(list(rows))


def _question_payload(exam):
    """Question text + choices WITHOUT correctness flags; cached per exam version in Redis."""
    key = f"exam:{exam.pk}:q:{int(exam.updated_at.timestamp() * 1_000_000)}"
    data = cache.get(key)
    if data is None:
        qs = Question.objects.filter(exam=exam).select_related("section").prefetch_related(
            Prefetch("choices", queryset=Choice.objects.only("id", "text", "question_id").order_by("id")))
        data = []
        for q in qs:
            opts = list(q.choices.all())
            item = {"id": q.id, "kind": q.kind, "points": float(q.points), "text": q.text,
                    "section": ({"id": q.section_id, "title": q.section.title, "instructions": q.section.instructions}
                                if q.section_id else None),
                    "choices": [{"id": c.id, "text": c.text} for c in opts if q.kind not in ("short", "fill")], "blanks": [{"n": c.pair_text, "percent": str(c.weight)} for c in opts if q.kind == "fill"]}
            if q.kind in ("match", "drag"):
                # the targets (right side / slots), in a fixed but non-obvious order; the answers stay on the server
                import hashlib
                targets = {c.pair_text for c in opts if c.pair_text}
                item["targets"] = sorted(targets, key=lambda t: hashlib.sha1(f"{q.id}:{t}".encode()).hexdigest())
            data.append(item)
        cache.set(key, data, 3600)
    return data


class StartExamView(ExamThrottled):
    permission_classes = [IsStudent]

    def post(self, request, pk):
        user = request.user
        exam = get_object_or_404(
            Exam.objects.filter(is_published=True, course__enrollments__student=user), pk=pk)
        now = timezone.now()
        if exam.is_suspended:
            return Response({"detail": "این آزمون موقتاً به تعلیق درآمده است."}, status=423)
        if not (exam.starts_at <= now < exam.ends_at):
            raise PermissionDenied("آزمون در این زمان در دسترس نیست.")
        deadline = min(now + timedelta(minutes=exam.duration_minutes), exam.ends_at)
        pool = _question_payload(exam)
        drawn = draw_questions(exam, [q["id"] for q in pool], user.pk)
        # Unique(exam, student) makes concurrent/double starts converge on one attempt (and one draw).
        attempt, created = Attempt.objects.get_or_create(
            exam=exam, student=user, defaults={"deadline": deadline, "question_ids": drawn})
        if attempt.submitted_at is not None:
            return Response({"detail": "این آزمون قبلاً ثبت نهایی شده است."}, status=409)
        if created:
            audit(request, "exam.start", f"exam:{exam.pk}")
        by_id = {q["id"]: q for q in pool}
        questions = []
        for qid in attempt.question_ids:
            q = by_id.get(qid)
            if q is None:
                continue
            choices = list(q["choices"])
            if exam.shuffle_choices and q["kind"] in ("mcq", "multi", "match", "drag"):
                random.Random(f"{exam.pk}:{user.pk}:{qid}").shuffle(choices)
            questions.append({**q, "choices": choices})
        saved, texts, multi, maps = {}, {}, {}, {}
        for qid, cid, text, cids, mp in Answer.objects.filter(attempt=attempt).values_list(
                "question_id", "choice_id", "text", "choice_ids", "mapping"):
            if cid is not None:
                saved[qid] = cid
            if text:
                texts[qid] = text
            if cids:
                multi[qid] = cids
            if mp:
                maps[qid] = mp
        return Response({"attempt": attempt.pk, "deadline": attempt.deadline, "questions": questions,
                         "saved_answers": saved, "saved_texts": texts, "saved_multi": multi, "saved_maps": maps,
                         "allow_back": attempt.exam.allow_back, "back_penalty": float(attempt.exam.back_penalty or 0),
                         "questions_per_screen": attempt.exam.questions_per_screen, "back_moves": attempt.back_moves})


class AnswerInput(serializers.Serializer):
    question = serializers.IntegerField()
    choice = serializers.IntegerField(required=False)
    choices = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=8)   # multi
    mapping = serializers.DictField(child=serializers.CharField(max_length=500), required=False)       # match / drag
    text = serializers.CharField(required=False, allow_blank=True, max_length=10000, trim_whitespace=False)
    blanks = serializers.DictField(child=serializers.CharField(max_length=500, allow_blank=True), required=False)  # fill


class AnswerView(ExamThrottled):
    """PUT is an idempotent upsert: retries and double-clicks are harmless."""
    permission_classes = [IsStudent]

    def put(self, request, pk):
        s = AnswerInput(data=request.data)
        s.is_valid(raise_exception=True)
        q_id = s.validated_data["question"]
        attempt = get_object_or_404(
            Attempt.objects.only("id", "exam_id", "deadline", "submitted_at", "question_ids"),
            pk=pk, student=request.user)
        now = timezone.now()
        if Exam.objects.filter(pk=attempt.exam_id, is_suspended=True).exists():
            return Response({"detail": "آزمون موقتاً به تعلیق درآمده است؛ پاسخ‌ها ذخیره نمی‌شوند."}, status=423)
        if attempt.submitted_at is not None or now > attempt.deadline + timedelta(seconds=GRACE_SECONDS):
            return Response({"detail": "زمان پاسخ‌دهی به پایان رسیده است."}, status=409)
        if attempt.question_ids and q_id not in attempt.question_ids:
            raise ValidationError({"question": "این سؤال در آزمون شما نیست."})
        question = Question.objects.filter(pk=q_id, exam_id=attempt.exam_id).values("kind").first()
        if question is None:
            raise ValidationError({"question": "سؤال نامعتبر است."})
        if question["kind"] == QuestionKind.ESSAY:
            if "text" not in s.validated_data:
                raise ValidationError({"text": "متن پاسخ لازم است."})
            choice_id, text = None, s.validated_data["text"]
        else:
            choice_id, text = s.validated_data.get("choice"), ""
            kind = question["kind"]
            if kind == QuestionKind.SHORT:
                text = s.validated_data.get("text", "").strip()[:500]
                choice_id = None
            elif kind == QuestionKind.FILL:
                import json
                valid = set(Choice.objects.filter(question_id=q_id).values_list("pair_text", flat=True))
                blanks = s.validated_data.get("blanks") or {}
                text = json.dumps({str(k): str(v).strip()[:500] for k, v in blanks.items() if str(k) in valid}, ensure_ascii=False)
                choice_id = None
            elif kind == QuestionKind.MULTI:
                ids = set(s.validated_data.get("choices", []))
                valid = set(Choice.objects.filter(question_id=q_id, pk__in=ids).values_list("pk", flat=True))
                if valid != ids:
                    raise ValidationError({"choices": "گزینه نامعتبر است."})
                multi = sorted(valid)
                Answer.objects.bulk_create(
                    [Answer(attempt_id=attempt.pk, question_id=q_id, choice_id=None, choice_ids=multi, answered_at=now)],
                    update_conflicts=True, unique_fields=["attempt", "question"], update_fields=["choice_ids", "answered_at"])
                return Response(status=204)
            elif kind in (QuestionKind.MATCH, QuestionKind.DRAG):
                mapping = {str(k): v for k, v in (s.validated_data.get("mapping") or {}).items()}
                valid = set(str(x) for x in Choice.objects.filter(question_id=q_id).values_list("pk", flat=True))
                if not set(mapping) <= valid:
                    raise ValidationError({"mapping": "مورد نامعتبر است."})
                Answer.objects.bulk_create(
                    [Answer(attempt_id=attempt.pk, question_id=q_id, choice_id=None, mapping=mapping, answered_at=now)],
                    update_conflicts=True, unique_fields=["attempt", "question"], update_fields=["mapping", "answered_at"])
                return Response(status=204)
            else:
                if choice_id is None or not Choice.objects.filter(pk=choice_id, question_id=q_id).exists():
                    raise ValidationError({"choice": "گزینه نامعتبر است."})
        Answer.objects.bulk_create(
            [Answer(attempt_id=attempt.pk, question_id=q_id, choice_id=choice_id, text=text, answered_at=now)],
            update_conflicts=True, unique_fields=["attempt", "question"],
            update_fields=["choice", "text", "answered_at"])
        return Response(status=204)


class SubmitView(ExamThrottled):
    permission_classes = [IsStudent]

    def post(self, request, pk):
        get_object_or_404(Attempt.objects.only("id"), pk=pk, student=request.user)
        attempt = submit_attempt(pk, student_id=request.user.pk)   # idempotent
        audit(request, "exam.submit", f"attempt:{attempt.pk}")
        from .services import result_for_student
        return Response(result_for_student(attempt))


class ExamInfoView(APIView):
    """Before the start: the title, the designer's instructions and the rules (for enrolled, published exams)."""
    permission_classes = [IsStudent]

    def get(self, request, pk):
        exam = get_object_or_404(Exam.objects.filter(is_published=True, course__enrollments__student=request.user), pk=pk)
        from apps.core.activity import record_view
        record_view(request, "exam.view", f"exam:{exam.pk}")
        from .guide import default_guide_html
        return Response({"title": exam.title, "instructions": exam.instructions or default_guide_html(exam.course.org if getattr(exam, "course_id", None) else None), "duration_minutes": exam.duration_minutes,
                         "starts_at": exam.starts_at, "ends_at": exam.ends_at, "suspended": exam.is_suspended,
                         "result_mode": exam.result_mode, "shuffle_questions": exam.shuffle_questions,
                         "shuffle_choices": exam.shuffle_choices})


class ResultView(APIView):
    """After the submit: what the designer allowed the student to see (score, counts, both or nothing)."""
    permission_classes = [IsStudent]

    def get(self, request, pk):
        attempt = get_object_or_404(Attempt.objects.select_related("exam"), pk=pk, student=request.user)
        if attempt.submitted_at is None:
            return Response({"detail": "هنوز ثبت نهایی نشده است."}, status=409)
        from apps.core.activity import record_view
        record_view(request, "exam.result_view", f"attempt:{attempt.pk}")
        from .services import result_for_student
        return Response(result_for_student(attempt))


class BackMoveView(APIView):
    """POST /api/attempts/<id>/back/ — the student went back to an earlier screen. Each return costs the exam's penalty
    (when the exam allows going back at all)."""
    permission_classes = [IsStudent]

    def post(self, request, pk):
        from decimal import Decimal
        attempt = get_object_or_404(Attempt.objects.select_related("exam"), pk=pk, student=request.user)
        if attempt.submitted_at:
            return Response({"detail": "آزمون ثبت شده است."}, status=409)
        if not attempt.exam.allow_back:
            return Response({"detail": "در این آزمون بازگشت به سؤال‌های قبلی مجاز نیست."}, status=403)
        Attempt.objects.filter(pk=attempt.pk).update(back_moves=F("back_moves") + 1)
        attempt.refresh_from_db(fields=["back_moves"])
        penalty = Decimal(attempt.back_moves) * (attempt.exam.back_penalty or Decimal(0))
        return Response({"back_moves": attempt.back_moves, "penalty_so_far": float(penalty)})
