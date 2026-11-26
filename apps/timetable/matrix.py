"""تقویم رنگی برنامه‌ی هفتگی: ردیف = کلاس، ستون = زنگ (برای یک روز). هر خانه: درس، دبیر و ساعت.
همچنین کسری ساعت درس‌ها (ساعت لازم منهای ساعت جاگرفته) و ساعت‌های خالی هر کلاس در هفته."""
from collections import defaultdict
from datetime import time as _time

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, GradeWeeklyHours
from apps.core.scope import active_school_id, org_in_scope
from apps.courses.models import Course

from .hours import bell_hours
from .models import BellPeriod, TimetableEntry, TimetableVersion
from .models import WEEKDAY_CHOICES

WEEKDAYS = dict(WEEKDAY_CHOICES)

ROLES = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec", "teacher")


def _hm(t):
    return t.strftime("%H:%M") if isinstance(t, _time) else ""


class TimetableMatrixView(APIView):
    """GET ?version=ID&day=WEEKDAY — تقویم رنگی یک روز؛ بدون version آخرین نسخه‌ی مدرسه‌ی کارتابل."""

    def get(self, request):
        u = request.user
        if u.role not in ROLES:
            raise PermissionDenied("تقویم برنامه برای این نقش نیست.")
        sid = active_school_id(request) or (request.query_params.get("school") if u.role == "super_admin" else None)
        if not sid:
            raise serializers.ValidationError({"school": "مدرسه را در کارتابل انتخاب کنید."})
        sid = int(sid)
        if u.role != "super_admin" and not org_in_scope(u, _school(sid)):
            raise PermissionDenied("این مدرسه در کارتابل شما نیست.")
        ver = None
        if (request.query_params.get("version") or "").isdigit():
            ver = TimetableVersion.objects.filter(pk=int(request.query_params["version"]), school_id=sid).first()
        else:
            ver = TimetableVersion.objects.filter(school_id=sid).order_by("-pk").first()
        if ver is None:
            return Response({"version": None, "message": "هنوز نسخه‌ای برای این مدرسه ساخته نشده است.", "days": [], "bells": [], "classes": [], "shortfall": [], "free": []})
        entries = list(TimetableEntry.objects.filter(version=ver).select_related("classroom", "period", "subject", "teacher"))
        bells = list(BellPeriod.objects.filter(school_id=sid).order_by("number"))
        bell_of = {b.number: b for b in bells}
        days = sorted({e.weekday for e in entries})
        day = int(request.query_params["day"]) if (request.query_params.get("day") or "").isdigit() else (days[0] if days else 0)
        if u.role == "teacher":
            entries = [e for e in entries if e.teacher_id == u.pk]
        # the day's grid: classes in rows, bells in columns
        cells = defaultdict(lambda: defaultdict(list))
        for e in entries:
            if e.weekday != day:
                continue
            hrs = e.piece_hours or bell_of.get(e.period.number, None) and bell_of[e.period.number].hours or 1
            cells[e.classroom_id][e.period.number].append({
                "id": e.pk, "lesson": e.subject.name if e.subject_id else "", "teacher": (e.teacher.get_full_name() or e.teacher.username) if e.teacher_id else "",
                "part": e.part, "hours": hrs, "color": _color(e.subject_id), "extra": bool(e.extra_day)})
        classes = ClassRoom.objects.filter(school_id=sid, academic_year__is_current=True).order_by("grade__name", "name")
        placed = defaultdict(int)
        for e in TimetableEntry.objects.filter(version=ver).select_related("period"):
            placed[(e.classroom_id, e.subject_id)] += e.piece_hours or (bell_of[e.period.number].hours if e.period.number in bell_of else 1)
        class_rows = []
        for c in classes:
            if c.pk not in {e.classroom_id for e in entries}:
                continue
            todo = []
            for co in Course.objects.filter(classroom=c, subject__isnull=False).select_related("subject", "teacher"):
                gw = GradeWeeklyHours.objects.filter(grade=c.grade, subject=co.subject, field=c.study_field).first() \
                    or GradeWeeklyHours.objects.filter(grade=c.grade, subject=co.subject, field__isnull=True).first()
                left = (gw.hours if gw else (co.subject.weekly_hours or 0)) - placed.get((c.pk, co.subject_id), 0)
                if left > 0:
                    todo.append({"subject": co.subject_id, "name": co.subject.name, "left": left,
                                 "teacher": (co.teacher.get_full_name() or co.teacher.username) if co.teacher_id else ""})
            class_rows.append({"id": c.pk, "name": c.name, "cells": {str(k): v for k, v in cells.get(c.pk, {}).items()},
                               "todo": todo})
        # shortfall: required hours of each lesson minus the hours placed in the week
        extra_by = defaultdict(int)
        extra_teacher = defaultdict(int)
        for e in TimetableEntry.objects.filter(version=ver, extra_day=True).select_related("teacher"):
            extra_by[(e.classroom_id, e.subject_id)] += 1
            if e.teacher_id:
                extra_teacher[(e.teacher.get_full_name() or e.teacher.username)] += 1
        shortfall = []
        for c in classes:
            for co in Course.objects.filter(classroom=c, subject__isnull=False).select_related("subject", "teacher"):
                gw = GradeWeeklyHours.objects.filter(grade=c.grade, subject=co.subject, field=c.study_field).first() \
                    or GradeWeeklyHours.objects.filter(grade=c.grade, subject=co.subject, field__isnull=True).first()
                need = gw.hours if gw else (co.subject.weekly_hours or 0)
                got = placed.get((c.pk, co.subject_id), 0)
                ex = extra_by.get((c.pk, co.subject_id), 0)
                if (need and got < need) or ex:
                    shortfall.append({"class": c.name, "lesson": co.subject.name,
                                      "teacher": (co.teacher.get_full_name() or co.teacher.username) if co.teacher_id else "",
                                      "required": need, "placed": got, "missing": max(0, need - got), "extra_days": ex})
        free = []
        week_days = days or [day]
        for c in classes:
            for d in week_days:
                for b in bells:
                    used = sum(x["hours"] for x in cells.get(c.pk, {}).get(b.number, []) if d == day) if d == day else None
                    if d != day:
                        continue
                    left = bell_hours(b.start_time, b.end_time) - (used or 0)
                    if left > 0 and c.pk in cells:
                        free.append({"class": c.name, "day": d, "bell": b.number, "hours": left})
        # the whole week: days in columns (each with its bells), classes in rows
        week_days = [0, 1, 2, 3, 4, 5]
        week_cells = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        for e in entries:
            if e.weekday not in week_days:
                continue
            hrs = e.piece_hours or (bell_of[e.period.number].hours if e.period.number in bell_of else 1)
            week_cells[e.classroom_id][e.weekday][e.period.number].append({
                "id": e.pk, "subject": e.subject_id, "lesson": e.subject.name if e.subject_id else "",
                "teacher": (e.teacher.get_full_name() or e.teacher.username) if e.teacher_id else "",
                "part": e.part, "hours": hrs, "color": _color(e.subject_id), "extra": bool(e.extra_day)})
        week_rows = []
        for row in class_rows:
            wc = week_cells.get(row["id"], {})
            week_rows.append({"id": row["id"], "name": row["name"], "todo": row["todo"],
                              "cells": {str(d): {str(b.number): wc.get(d, {}).get(b.number, []) for b in bells} for d in week_days}})
        return Response({
            "version": ver.pk, "title": ver.title, "status": ver.status, "editable": ver.status in ("draft", "review"),
            "week": {"days": [{"id": d, "name": WEEKDAYS.get(d, str(d))} for d in week_days],
                     "bells": [{"number": b.number, "start": _hm(b.start_time), "end": _hm(b.end_time), "hours": b.hours} for b in bells],
                     "classes": week_rows},
            "days": [{"id": d, "name": WEEKDAYS.get(d, str(d))} for d in days], "day": day, "day_name": WEEKDAYS.get(day, ""),
            "bells": [{"number": b.number, "start": _hm(b.start_time), "end": _hm(b.end_time), "hours": b.hours} for b in bells],
            "classes": class_rows, "shortfall": shortfall, "free": free,
            "extra_teachers": [{"teacher": k, "pieces": v} for k, v in sorted(extra_teacher.items())],
        })


def _school(pk):
    from apps.accounts.models import OrgUnit
    return OrgUnit.objects.filter(pk=pk).first()


_PALETTE = ["#d6eaf8", "#d5f5e3", "#fdebd0", "#e8daef", "#fadbd8", "#d4efdf", "#fcf3cf", "#d1f2eb", "#f6ddcc", "#eaecee"]


def _color(subject_id):
    return _PALETTE[(subject_id or 0) % len(_PALETTE)]
