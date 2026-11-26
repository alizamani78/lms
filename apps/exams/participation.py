"""Who took the exam: completed, interrupted (connection lost in the middle), in progress or not attended.
A teacher / school admin / higher admin can send an interrupted student back into the exam."""
import csv
import io
from datetime import timedelta

from django.db.models import Max
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit
from apps.courses.models import Enrollment

from .authoring import staff_exams
from .models import Answer, Attempt, Exam

SILENCE = timedelta(minutes=3)       # no answer for this long while the time is still running = the line dropped


def _status(attempt, now):
    if attempt is None:
        return "absent"
    if attempt.submitted_at:
        return "completed"
    if attempt.deadline <= now:
        return "interrupted"               # time ran out without a final submit: the student's line was lost
    last = attempt.last_seen or attempt.started_at
    return "interrupted" if now - last > SILENCE else "in_progress"


def _rows(exam, now):
    enrolled = list(Enrollment.objects.filter(course=exam.course).select_related("student").order_by("student__last_name", "student__username"))
    attempts = {a.student_id: a for a in Attempt.objects.filter(exam=exam)}
    last = dict(Answer.objects.filter(attempt__exam=exam).values_list("attempt_id").annotate(t=Max("answered_at")))
    out = []
    for e in enrolled:
        s = e.student
        a = attempts.get(s.pk)
        if a is not None:
            a.last_seen = last.get(a.pk) or a.started_at
        status = _status(a, now)
        out.append({"student_id": s.pk, "name": s.get_full_name() or s.username, "username": s.username,
                    "status": status, "label": {"completed": "کامل امتحان داد", "interrupted": "قطع شده (وسط آزمون)",
                                                "in_progress": "در حال آزمون", "absent": "شرکت نکرده"}[status],
                    "attempt": a.pk if a else None,
                    "started_at": a.started_at.isoformat() if a else "",
                    "last_seen": a.last_seen.isoformat() if a and a.last_seen else "",
                    "submitted_at": a.submitted_at.isoformat() if a and a.submitted_at else "",
                    "score": float(a.score) if a and a.score is not None else None,
                    "can_resume": status in ("interrupted", "absent") and a is not None and a.submitted_at is None})
    return out


class ParticipationView(APIView):
    """GET /api/exams/<id>/participation/  (?format=csv for the report file)"""

    def get(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)
        rows = _rows(exam, timezone.now())
        counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("completed", "interrupted", "in_progress", "absent")}
        if request.query_params.get("format") == "csv":
            buf = io.StringIO()
            buf.write("\ufeff")
            w = csv.writer(buf)
            w.writerow(["نام", "نام کاربری", "وضعیت", "شروع", "آخرین فعالیت", "ثبت نهایی", "نمره"])
            for r in rows:
                w.writerow([r["name"], r["username"], r["label"], r["started_at"], r["last_seen"], r["submitted_at"],
                            "" if r["score"] is None else r["score"]])
            resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="participation-{exam.pk}.csv"'
            resp["Cache-Control"] = "private, no-store"
            return resp
        return Response({"exam": exam.title, "suspended": exam.is_suspended, "starts_at": exam.starts_at,
                         "ends_at": exam.ends_at, "counts": counts, "rows": rows})


class ResumeView(APIView):
    """POST /api/exams/<id>/participation/<attempt>/resume/ — gives the student a fresh window (the exam's duration,
    never beyond the exam's end). The answers already saved stay; the student continues where he stopped."""

    def post(self, request, pk, attempt_id):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)
        a = get_object_or_404(Attempt, pk=attempt_id, exam=exam)
        if a.submitted_at is not None:
            raise serializers.ValidationError({"detail": "این شرکت‌کننده قبلاً آزمون را ثبت نهایی کرده است."})
        now = timezone.now()
        if now >= exam.ends_at:
            raise serializers.ValidationError({"detail": "زمان آزمون تمام شده است؛ پایان آزمون را تمدید کنید، سپس ادامه دهید."})
        a.deadline = min(now + timedelta(minutes=exam.duration_minutes), exam.ends_at)
        a.save(update_fields=["deadline"])
        audit(request, "exam.resume", f"exam:{exam.pk} attempt:{a.pk}")
        return Response({"attempt": a.pk, "deadline": a.deadline})
