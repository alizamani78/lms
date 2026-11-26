"""ساعات حضور دبیر by day: one day, several bell periods of one teacher in one step (duplicates are not added)."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.scope import org_in_scope

from .models import BellPeriod, TeacherAvailability

ADDERS = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec")


def _check(user, school):
    if user.role not in ADDERS or not org_in_scope(user, school):
        raise PermissionDenied("ساعات حضور دبیر را مدیر یا معاونان مدرسه ثبت می‌کنند.")


class PresencePeriodsView(APIView):
    """GET ?school=ID — the bell periods of the school."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        rows = BellPeriod.objects.filter(school=school).order_by("number")
        return Response([{"id": b.pk, "label": f"زنگ {b.number} ({b.start_time:%H:%M}–{b.end_time:%H:%M})"} for b in rows])


class PresenceBulkView(APIView):
    """POST {teacher, weekday, periods:[ids]} — the teacher is present in these periods of that day."""

    def post(self, request):
        teacher = get_object_or_404(User, pk=request.data.get("teacher") or 0, role="teacher")
        try:
            day = int(request.data.get("weekday"))
        except (TypeError, ValueError):
            raise serializers.ValidationError({"weekday": "روز را انتخاب کنید."})
        ids = [int(x) for x in (request.data.get("periods") or []) if str(x).isdigit()]
        if not ids:
            raise serializers.ValidationError({"periods": "حداقل یک زنگ را انتخاب کنید."})
        periods = list(BellPeriod.objects.filter(pk__in=ids).select_related("school"))
        if not periods:
            raise serializers.ValidationError({"periods": "زنگ‌های انتخاب‌شده معتبر نیستند."})
        for b in periods:
            _check(request.user, b.school)
        added, duplicates = [], []
        for b in periods:
            _, created = TeacherAvailability.objects.get_or_create(teacher=teacher, weekday=day, period=b)
            (added if created else duplicates).append(b.number)
        audit(request, "presence.bulk", f"teacher:{teacher.pk} day:{day} added={len(added)}")
        msg = f"{len(added)} زنگ برای {teacher.get_full_name() or teacher.username} ثبت شد"
        if duplicates:
            msg += f"؛ زنگ‌های {', '.join(str(x) for x in duplicates)} از قبل ثبت بود و دوباره ثبت نشد"
        return Response({"added": added, "duplicates": duplicates, "message": msg}, status=201)


DAY_NAMES = ["شنبه", "یک‌شنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


class PresenceGridView(APIView):
    """GET ?school=ID&teacher=ID — جدول روز (ردیف) × زنگ (ستون) و خانه‌های حضور دبیر.
    PUT {school, teacher, selected:[[روز, زنگ‌id], …]} — ثبت حضور؛ خانه‌ی انتخاب‌نشده یعنی عدم حضور."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        periods = list(BellPeriod.objects.filter(school=school).order_by("number"))
        selected = []
        if request.query_params.get("teacher"):
            selected = [[a.weekday, a.period_id] for a in TeacherAvailability.objects.filter(
                teacher_id=request.query_params["teacher"], period__school=school)]
        return Response({
            "periods": [{"id": b.pk, "number": b.number, "label": f"{b.start_time:%H:%M}–{b.end_time:%H:%M}"} for b in periods],
            "days": [{"id": i, "name": n} for i, n in enumerate(DAY_NAMES)],
            "selected": selected,
        })

    def put(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _check(request.user, school)
        teacher = get_object_or_404(User, pk=request.data.get("teacher") or 0, role="teacher")
        valid = set(BellPeriod.objects.filter(school=school).values_list("pk", flat=True))
        want = set()
        for item in request.data.get("selected") or []:
            try:
                day, pid = int(item[0]), int(item[1])
            except (TypeError, ValueError, IndexError):
                raise serializers.ValidationError({"selected": "خانه‌های انتخاب‌شده درست نیستند."})
            if 0 <= day <= 6 and pid in valid:
                want.add((day, pid))
        have = set(TeacherAvailability.objects.filter(teacher=teacher, period__school=school).values_list("weekday", "period_id"))
        for day, pid in want - have:
            TeacherAvailability.objects.get_or_create(teacher=teacher, weekday=day, period_id=pid)
        for day, pid in have - want:
            TeacherAvailability.objects.filter(teacher=teacher, weekday=day, period_id=pid).delete()
        audit(request, "presence.grid", f"teacher:{teacher.pk} cells:{len(want)}")
        return Response({"ok": True, "cells": len(want)})
