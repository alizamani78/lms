"""Student school history: visible to the student, to the admins of every school in the trail (the new school
included), to verified parents and to the teachers of the student's current school."""
from datetime import date as date_cls, timedelta

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import ParentLink, User
from apps.attendance.models import AttendanceRecord
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope
from apps.exams.models import Attempt

from .models import StudentSchoolHistory


def _can_see(user, student):
    if user.pk == student.pk or user.role == User.Role.SUPER_ADMIN:
        return True
    trail = StudentSchoolHistory.objects.filter(student=student).select_related("school")
    if user.role in User.ADMIN_ROLES or user.role == User.Role.EXPERT:
        return any(org_in_scope(user, h.school) for h in trail)
    if user.role == User.Role.TEACHER:
        from apps.courses.models import Enrollment
        return Enrollment.objects.filter(student=student, course__teacher=user).exists()
    if user.role == User.Role.PARENT:
        return ParentLink.objects.filter(parent=user, student=student, is_verified=True).exists()
    return False


def _period_summary(student, h):
    end = h.end_date or timezone.localdate()
    att = AttendanceRecord.objects.filter(student=student, classroom__school=h.school, date__range=(h.start_date, end))
    att_counts = att.aggregate(total=Count("id"), absent=Count("id", filter=Q(status="absent")),
                               late=Count("id", filter=Q(status="late")))
    exams = (Attempt.objects.filter(student=student, exam__course__org=h.school, submitted_at__date__range=(h.start_date, end),
                                    submitted_at__isnull=False, total__gt=0)
             .values("exam__course__title").distinct())
    titles = sorted({e["exam__course__title"] for e in exams})
    return {"attendance_days": att_counts["total"] or 0, "absent": att_counts["absent"] or 0, "late": att_counts["late"] or 0,
            "courses": titles}


class StudentHistoryView(APIView):
    """GET /api/students/<id>/history/?year=<academic year id>  (default: current year)"""
    permission_classes = [role_required(*User.READ_ROLES, User.Role.STUDENT, User.Role.PARENT)]

    def get(self, request, pk):
        student = get_object_or_404(User, pk=pk, role=User.Role.STUDENT)
        if not _can_see(request.user, student):
            return Response(status=404)
        qs = StudentSchoolHistory.objects.filter(student=student).select_related("school", "academic_year")
        year = request.query_params.get("year", "")
        if year.isdigit():
            qs = qs.filter(academic_year_id=int(year))
        else:
            qs = qs.filter(academic_year__is_current=True) | qs.filter(academic_year__isnull=True)
        out = []
        for h in qs:
            out.append({"school": h.school.name, "school_id": h.school_id, "from": h.start_date.isoformat(),
                        "to": h.end_date.isoformat() if h.end_date else None, "current": h.end_date is None,
                        "reason": h.reason, "year": h.academic_year.name if h.academic_year_id else "",
                        "summary": _period_summary(student, h)})
        return Response({"student": student.get_full_name() or student.username, "periods": out})
