from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.core.models import record_grade_change
from apps.coursework.models import Submission
from apps.exams.models import Answer, Attempt, QuestionKind
from apps.exams.services import compute_scores

from .models import GradeAppeal


def window():
    return timedelta(days=getattr(settings, "APPEAL_WINDOW_DAYS", 7))


def resolve_item(kind, ref_id, student):
    """The graded item a student may appeal, or None. Only the student's OWN, already-graded, visible results."""
    if kind == "answer":
        a = Answer.objects.select_related("question", "attempt__exam__course").filter(
            pk=ref_id, attempt__student=student, question__kind=QuestionKind.ESSAY, points_awarded__isnull=False,
            attempt__exam__show_results=True).first()
        if a:
            return {"course": a.attempt.exam.course, "points": a.points_awarded, "max": a.question.points,
                    "graded_at": a.graded_at, "label": f"{a.attempt.exam.title} — {a.question.text[:70]}"}
    elif kind == "submission":
        s = Submission.objects.select_related("assignment__course").filter(
            pk=ref_id, student=student, graded_at__isnull=False).first()
        if s:
            return {"course": s.assignment.course, "points": s.points, "max": s.assignment.max_points,
                    "graded_at": s.graded_at, "label": f"تکلیف: {s.assignment.title}"}
    elif kind == "attempt":
        t = Attempt.objects.select_related("exam__course").filter(
            pk=ref_id, student=student, submitted_at__isnull=False, grading_status="graded",
            exam__show_results=True).first()
        if t:
            return {"course": t.exam.course, "points": t.score, "max": t.total, "graded_at": t.submitted_at,
                    "label": f"کل آزمون: {t.exam.title}"}
    return None


def eligible(student):
    cutoff = timezone.now() - window()
    done = set(GradeAppeal.objects.filter(student=student).values_list("kind", "ref_id"))
    out = []
    for a in Answer.objects.select_related("question", "attempt__exam").filter(
            attempt__student=student, question__kind=QuestionKind.ESSAY, points_awarded__isnull=False,
            attempt__exam__show_results=True, graded_at__gte=cutoff).order_by("-graded_at")[:50]:
        if ("answer", a.pk) not in done:
            out.append({"kind": "answer", "ref_id": a.pk, "points": float(a.points_awarded), "max": float(a.question.points),
                        "label": f"{a.attempt.exam.title} — {a.question.text[:70]}"})
    for s in Submission.objects.select_related("assignment").filter(student=student, graded_at__gte=cutoff) \
            .order_by("-graded_at")[:50]:
        if ("submission", s.pk) not in done:
            out.append({"kind": "submission", "ref_id": s.pk, "points": float(s.points), "max": float(s.assignment.max_points),
                        "label": f"تکلیف: {s.assignment.title}"})
    for t in Attempt.objects.select_related("exam").filter(
            student=student, submitted_at__gte=cutoff, grading_status="graded", exam__show_results=True) \
            .order_by("-submitted_at")[:50]:
        if ("attempt", t.pk) not in done:
            out.append({"kind": "attempt", "ref_id": t.pk, "points": float(t.score), "max": float(t.total),
                        "label": f"کل آزمون: {t.exam.title}"})
    return out


def apply_decision(request, appeal, new_points):
    """Change the grade through the same paths teachers use, writing the grade history. Raises ValidationError."""
    reason = f"اعتراض #{appeal.pk} پذیرفته شد"
    new = Decimal(new_points)
    if appeal.kind == "answer":
        ans = Answer.objects.select_for_update(of=("self",)).select_related("question").get(pk=appeal.ref_id)
        if new > ans.question.points:
            raise serializers.ValidationError({"new_points": f"حداکثر نمره این سؤال {ans.question.points} است."})
        old = ans.points_awarded
        ans.points_awarded, ans.graded_by, ans.graded_at = new, request.user, timezone.now()
        ans.save(update_fields=["points_awarded", "graded_by", "graded_at"])
        attempt = Attempt.objects.select_for_update().get(pk=ans.attempt_id)
        attempt.score, attempt.total, pending = compute_scores(attempt)
        attempt.grading_status = Attempt.Grading.PENDING if pending else Attempt.Grading.GRADED
        attempt.save(update_fields=["score", "total", "grading_status"])
        record_grade_change(request, "answer", ans.pk, appeal.student_id, old, new, reason)
    elif appeal.kind == "submission":
        sub = Submission.objects.select_for_update(of=("self",)).select_related("assignment").get(pk=appeal.ref_id)
        if new > sub.assignment.max_points:
            raise serializers.ValidationError({"new_points": f"حداکثر نمره {sub.assignment.max_points} است."})
        old = sub.points
        sub.points, sub.graded_by, sub.graded_at = new, request.user, timezone.now()
        sub.save(update_fields=["points", "graded_by", "graded_at"])
        record_grade_change(request, "submission", sub.pk, appeal.student_id, old, new, reason)
    else:
        attempt = Attempt.objects.select_for_update().get(pk=appeal.ref_id)
        score_now, total, _ = compute_scores(attempt)
        if new > total:
            raise serializers.ValidationError({"new_points": f"حداکثر نمره آزمون {total} است."})
        base = score_now - (attempt.adjustment or Decimal(0))
        old = attempt.score
        attempt.adjustment, attempt.score = new - base, new
        attempt.save(update_fields=["adjustment", "score"])
        record_grade_change(request, "attempt", attempt.pk, appeal.student_id, old, new, reason)
