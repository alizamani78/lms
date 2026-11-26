"""Manual grading of essay answers, with an append-only change history."""
from decimal import Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from apps.core.models import audit, record_grade_change

from .authoring import AuthoringView, staff_exams
from .models import Answer, Attempt, QuestionKind
from .services import compute_scores


class EssayListView(AuthoringView):
    """GET /api/exams/<id>/essays/?pending=1 — essay answers of submitted attempts."""

    def get(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)
        qs = (Answer.objects.filter(attempt__exam=exam, question__kind=QuestionKind.ESSAY,
                                    attempt__submitted_at__isnull=False)
              .select_related("question", "attempt__student").order_by("id"))
        if request.query_params.get("pending"):
            qs = qs.filter(points_awarded__isnull=True).exclude(text="")
        try:
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError:
            offset = 0
        rows = [{"id": a.pk, "student": a.attempt.student.username,
                 "student_name": a.attempt.student.get_full_name(), "question": a.question.text,
                 "max_points": float(a.question.points), "text": a.text,
                 "points_awarded": None if a.points_awarded is None else float(a.points_awarded),
                 "feedback": a.feedback} for a in qs[offset:offset + 100]]
        return Response(rows)


class GradeInput(serializers.Serializer):
    points = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0)
    feedback = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class GradeAnswerView(AuthoringView):
    """POST /api/answers/<id>/grade/ — changing an existing grade requires a reason; every change is logged."""

    def post(self, request, pk):
        s = GradeInput(data=request.data)
        s.is_valid(raise_exception=True)
        points, reason = s.validated_data["points"], s.validated_data.get("reason", "").strip()
        with transaction.atomic():
            answer = get_object_or_404(
                Answer.objects.select_for_update(of=("self",)).select_related("question", "attempt"), pk=pk,
                attempt__exam__in=staff_exams(request.user), question__kind=QuestionKind.ESSAY)
            attempt = Attempt.objects.select_for_update().get(pk=answer.attempt_id)
            if attempt.submitted_at is None:
                return Response({"detail": "پاسخ‌نامه هنوز ثبت نهایی نشده است."}, status=409)
            if points > answer.question.points:
                raise serializers.ValidationError({"points": f"حداکثر نمره این سؤال {answer.question.points} است."})
            old = answer.points_awarded
            if old is not None and old != points and not reason:
                raise serializers.ValidationError({"reason": "برای تغییر نمره، ذکر دلیل الزامی است."})
            if old != points:
                record_grade_change(request, "answer", answer.pk, attempt.student_id, old, points, reason)
            answer.points_awarded, answer.graded_by, answer.graded_at = points, request.user, timezone.now()
            answer.feedback = s.validated_data.get("feedback", answer.feedback)
            answer.save(update_fields=["points_awarded", "graded_by", "graded_at", "feedback"])
            attempt.score, attempt.total, pending = compute_scores(attempt)
            attempt.grading_status = Attempt.Grading.PENDING if pending else Attempt.Grading.GRADED
            attempt.save(update_fields=["score", "total", "grading_status"])
        audit(request, "grade.answer", f"answer:{answer.pk}")
        return Response({"answer": answer.pk, "attempt_score": float(attempt.score or Decimal(0)),
                         "grading_status": attempt.grading_status})
