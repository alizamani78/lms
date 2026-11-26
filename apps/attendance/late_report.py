"""گزارش تأخیرها: تاریخ، زنگ، دبیر، دانش‌آموز، کلاس و مدت تأخیر؛ محدود به مدرسه‌ی کارتابل و بازه‌ی تاریخ.
مدیر و معاون همه‌ی تأخیرهای مدرسه‌ی کارتابل را می‌بینند؛ معلم فقط زنگ‌های خودش را."""
from datetime import date as _date

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.jalali import jalali_str
from apps.core.scope import active_school_id

from .models import AttendanceRecord

ROLES = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec", "teacher")


class LateReportView(APIView):
    """GET ?from=YYYY-MM-DD&to=YYYY-MM-DD&classroom=ID — تأخیرهای ثبت‌شده."""

    def get(self, request):
        u = request.user
        if u.role not in ROLES:
            raise PermissionDenied("گزارش تأخیرها برای این نقش نیست.")
        qs = AttendanceRecord.objects.filter(status=AttendanceRecord.Status.LATE).select_related(
            "student", "classroom", "course", "course__teacher")
        sid = active_school_id(request) or (request.query_params.get("school") if u.role == "super_admin" else None)
        if not sid:
            raise serializers.ValidationError({"school": "مدرسه را در کارتابل انتخاب کنید."})
        qs = qs.filter(classroom__school_id=int(sid))
        if u.role == "teacher":
            qs = qs.filter(course__teacher=u)
        try:
            if request.query_params.get("from"):
                qs = qs.filter(date__gte=_date.fromisoformat(request.query_params["from"]))
            if request.query_params.get("to"):
                qs = qs.filter(date__lte=_date.fromisoformat(request.query_params["to"]))
        except ValueError:
            raise serializers.ValidationError({"date": "تاریخ باید به شکل YYYY-MM-DD باشد."})
        if (request.query_params.get("classroom") or "").isdigit():
            qs = qs.filter(classroom_id=int(request.query_params["classroom"]))
        rows = []
        for r in qs.order_by("-date", "period", "student__last_name")[:2000]:
            teacher = r.course.teacher if r.course_id and r.course.teacher_id else None
            rows.append({
                "date": r.date.isoformat(), "date_jalali": jalali_str(r.date), "period": r.period or "",
                "teacher": (teacher.get_full_name() or teacher.username) if teacher else "",
                "student": r.student.get_full_name() or r.student.username, "student_code": r.student.student_code or "",
                "classroom": r.classroom.name, "course": r.course.title if r.course_id else "",
                "minutes_late": r.minutes_late or 0,
            })
        return Response({"count": len(rows), "total_minutes": sum(x["minutes_late"] for x in rows), "rows": rows})


class AbsenceHoursView(APIView):
    """GET ?from&to&classroom — غیبت به ساعت و دقیقه برای هر دانش‌آموز.
    کل روز (همه‌ی زنگ‌های آن روز غایب باشد) = جمع ساعت زنگ‌های آن روز؛ وگرنه ساعت هر زنگ غایب؛ دقیقه‌ها جداگانه."""

    def get(self, request):
        from collections import defaultdict
        from apps.timetable.models import BellPeriod
        u = request.user
        if u.role not in ROLES:
            raise PermissionDenied("گزارش غیبت برای این نقش نیست.")
        sid = active_school_id(request) or (request.query_params.get("school") if u.role == "super_admin" else None)
        if not sid:
            raise serializers.ValidationError({"school": "مدرسه را در کارتابل انتخاب کنید."})
        qs = AttendanceRecord.objects.filter(status=AttendanceRecord.Status.ABSENT, classroom__school_id=int(sid)).select_related("student", "classroom")
        if u.role == "teacher":
            qs = qs.filter(course__teacher=u)
        try:
            if request.query_params.get("from"):
                qs = qs.filter(date__gte=_date.fromisoformat(request.query_params["from"]))
            if request.query_params.get("to"):
                qs = qs.filter(date__lte=_date.fromisoformat(request.query_params["to"]))
        except ValueError:
            raise serializers.ValidationError({"date": "تاریخ باید به شکل YYYY-MM-DD باشد."})
        bells = {b.number: b for b in BellPeriod.objects.filter(school_id=int(sid))}
        day_total = sum(b.hours for b in bells.values())
        per_day = defaultdict(lambda: defaultdict(set))
        names = {}
        for r in qs:
            names[r.student_id] = (r.student.get_full_name() or r.student.username, r.student.student_code or "", r.classroom.name)
            per_day[(r.student_id, r.date)]["periods"].add(r.period or 0)
            per_day[(r.student_id, r.date)]["minutes"] += r.absence_minutes or 0
            per_day[(r.student_id, r.date)]["hours"] += r.absence_hours or 0
        totals = defaultdict(lambda: {"hours": 0, "minutes": 0, "days": 0})
        for (stu, day), v in per_day.items():
            all_day = day_total and len(v["periods"] - {0}) >= len(bells)
            if all_day:
                h, m = day_total, 0
            else:
                h, m = v["hours"], v["minutes"]
            t = totals[stu]
            t["hours"] += h
            t["minutes"] += m
            t["days"] += 1 if all_day else 0
        out = []
        for stu, t in totals.items():
            hours_total = t["hours"] + t["minutes"] // 60
            name, code, klass = names[stu]
            out.append({"student": name, "student_code": code, "classroom": klass, "hours": hours_total,
                        "minutes": t["minutes"] % 60, "whole_days": t["days"]})
        return Response({"rows": out, "day_hours": day_total})
