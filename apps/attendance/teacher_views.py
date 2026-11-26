from datetime import date as date_cls

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, SchoolMembership, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope

from .models import TeacherAttendance

IsAdmin = role_required(*User.ADMIN_ROLES)


def _school(request, pk):
    school = get_object_or_404(OrgUnit, pk=pk, kind=OrgUnit.Kind.SCHOOL)
    if not org_in_scope(request.user, school):
        raise PermissionDenied()
    return school


def _teachers(school):
    ids = set(SchoolMembership.objects.filter(school=school).values_list("user_id", flat=True))
    return User.objects.filter(role="teacher").filter(pk__in=ids).order_by("last_name", "username") | \
        User.objects.filter(role="teacher", org=school).order_by("last_name", "username")


def _teachers_on(school, day):
    """Only the teachers who have a class or an exam on this day: a lesson in the approved weekly schedule
    (Saturday = 0 … Friday = 6), an online session, or an exam of the exam schedule."""
    from apps.courses.models import Course
    from apps.exams.models import ExamScheduleEntry
    from apps.liveclass.models import ClassSession
    from apps.timetable.models import TimetableEntry, TimetableVersion
    weekday = (day.weekday() + 2) % 7
    ids = set()
    version = TimetableVersion.objects.filter(school=school, status="approved").order_by("-approved_at", "-id").first()
    if version is not None:
        ids |= set(TimetableEntry.objects.filter(version=version, weekday=weekday, teacher__isnull=False)
                   .values_list("teacher_id", flat=True))
    ids |= set(ClassSession.objects.filter(course__org=school, starts_at__date=day, course__teacher__isnull=False)
               .values_list("course__teacher_id", flat=True))
    for e in ExamScheduleEntry.objects.filter(schedule__school=school, date=day):
        ids |= set(Course.objects.filter(classroom_id=e.classroom_id, subject_id=e.subject_id, teacher__isnull=False)
                   .values_list("teacher_id", flat=True))
    return _teachers(school).filter(pk__in=ids)


class TeacherAttendanceView(APIView):
    """GET /api/teacher-attendance/?school=<id>&date=YYYY-MM-DD  |  POST {school, date, records:[{teacher,status,note}]}"""
    permission_classes = [IsAdmin]

    def get(self, request):
        p = request.query_params
        if not p.get("school", "").isdigit():
            raise serializers.ValidationError({"school": "مدرسه لازم است."})
        school = _school(request, int(p["school"]))
        try:
            day = date_cls.fromisoformat(p.get("date") or timezone.localdate().isoformat())
        except ValueError:
            raise serializers.ValidationError({"date": "تاریخ نامعتبر است."})
        marks = {r.teacher_id: r for r in TeacherAttendance.objects.filter(school=school, date=day)}
        return Response([{"teacher": t.pk, "name": t.get_full_name() or t.username, "username": t.username,
                          "national": t.national_id or "",
                          "status": marks[t.pk].status if t.pk in marks else "",
                          "note": marks[t.pk].note if t.pk in marks else ""} for t in _teachers_on(school, day)[:300]])

    def post(self, request):
        s = request.data or {}
        school = _school(request, int(s.get("school") or 0)) if str(s.get("school", "")).isdigit() else None
        if school is None:
            raise serializers.ValidationError({"school": "مدرسه نامعتبر است."})
        try:
            day = date_cls.fromisoformat(str(s.get("date", "")))
        except ValueError:
            raise serializers.ValidationError({"date": "تاریخ نامعتبر است."})
        if day > timezone.localdate():
            raise serializers.ValidationError({"date": "ثبت حضور برای تاریخ آینده مجاز نیست."})
        allowed = {t.pk for t in _teachers_on(school, day)}
        recs = s.get("records") or []
        if not isinstance(recs, list) or len(recs) > 300:
            raise serializers.ValidationError({"records": "فهرست نامعتبر است."})
        valid = {c for c, _ in TeacherAttendance.Status.choices}
        created = 0
        with transaction.atomic():
            for r in recs:
                tid, st = r.get("teacher"), r.get("status")
                if tid not in allowed or st not in valid:
                    raise serializers.ValidationError({"records": "معلم یا وضعیت نامعتبر است (معلم باید عضو کادر این مدرسه باشد)."})
                _, is_new = TeacherAttendance.objects.update_or_create(
                    teacher_id=tid, school=school, date=day,
                    defaults={"status": st, "note": str(r.get("note", ""))[:200], "recorded_by": request.user})
                created += is_new
        audit(request, "teacher_attendance.record", f"school:{school.pk} {day} n={len(recs)}")
        return Response({"saved": len(recs), "new": created})
