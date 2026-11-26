"""نمره‌ی ماهیانه: معلم برای هر دانش‌آموز و هر ماه نیم‌سال، نمره‌ی ماهیانه ثبت یا ویرایش می‌کند.
اگر نمره‌ای ثبت نشده باشد، میانگین نمرات سرکلاسی همان ماه (از اول نیم‌سال تا امروز) نمایش داده می‌شود."""
import datetime as dt
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.jalali import jalali_to_gregorian
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course
from apps.evaluation.models import ClassGrade

from .grade_rules import TERM_MONTHS, monthly_average, monthly_value

MONTH_NAMES = {7: "مهر", 8: "آبان", 9: "آذر", 10: "دی", 11: "بهمن", 12: "اسفند",
               1: "فروردین", 2: "اردیبهشت", 3: "خرداد", 4: "تیر", 5: "مرداد", 6: "شهریور"}


def _course(request, pk):
    course = get_object_or_404(Course.objects.select_related("org", "teacher"), pk=pk)
    if not (course.teacher_id == request.user.pk or request.user.role == "super_admin" or
            (request.user.role in ("school_admin", "school_deputy_edu") and org_in_scope(request.user, course.org))):
        raise PermissionDenied("نمره‌ی ماهیانه را دبیر همان درس ثبت می‌کند.")
    return course


def _month_start_end(term, jm):
    """بازه‌ی میلادی یک ماه شمسی (jm) از نیم‌سال؛ تاریخ پایان ماه (یا امروز اگر ماه جاری است)."""
    import calendar
    today = timezone.localdate()
    jy = 1405 if jm >= 7 else 1406
    if term == "دوم" and jm in (1, 2, 3, 4, 5, 6):
        jy = 1406
    start = dt.date(*jalali_to_gregorian(jy, jm, 1))
    nxt = jm + 1 if jm < 12 else 1
    nyear = jy if jm < 12 else jy + 1
    end = dt.date(*jalali_to_gregorian(nyear, nxt, 1)) - dt.timedelta(days=1)
    return start, min(end, today)


class MonthlyGradesView(APIView):
    """GET ?course=ID&term=اول|دوم&month=JM — دانش‌آموزان درس با نمره‌ی ماهیانه‌ی ثبت‌شده و میانگین محاسبه‌شده.
    POST {course, term, month, scores:{student_id: نمره}} — ثبت یا ویرایش نمره‌ی ماهیانه (خالی یعنی حذف)."""

    def get(self, request):
        course = _course(request, request.query_params.get("course") or 0)
        term = request.query_params.get("term") or "اول"
        jm = int(request.query_params.get("month") or TERM_MONTHS[term][0])
        if jm not in TERM_MONTHS.get(term, ()):
            raise serializers.ValidationError({"month": "این ماه در نیم‌سال انتخاب‌شده نیست."})
        start, end = _month_start_end(term, jm)
        students = User.objects.filter(classrooms=course.classroom_id, role="student").order_by("last_name", "first_name", "id") if course.classroom_id else User.objects.none()
        rows = []
        for s in students:
            cls_grades = [(g.date, g.score, g.max_score) for g in ClassGrade.objects.filter(course=course, student=s, period="class", date__gte=start, date__lte=end)]
            computed = monthly_average(cls_grades, term, end)
            teacher = ClassGrade.objects.filter(course=course, student=s, period="monthly", title=f"ماهانه {MONTH_NAMES[jm]}").first()
            rows.append({"id": s.pk, "name": s.get_full_name() or s.username, "code": s.student_code or "",
                         "computed": float(computed) if computed is not None else None,
                         "teacher": float(teacher.score) if teacher else None,
                         "final": float(monthly_value(computed, teacher.score if teacher else None)) if (computed is not None or teacher) else None})
        return Response({"month": jm, "month_name": MONTH_NAMES[jm], "from": start.isoformat(), "to": end.isoformat(), "students": rows})

    def post(self, request):
        course = _course(request, request.data.get("course") or 0)
        term = request.data.get("term") or "اول"
        jm = int(request.data.get("month") or 0)
        if jm not in TERM_MONTHS.get(term, ()):
            raise serializers.ValidationError({"month": "این ماه در نیم‌سال انتخاب‌شده نیست."})
        saved = 0
        for sid, raw in (request.data.get("scores") or {}).items():
            title = f"ماهانه {MONTH_NAMES[jm]}"
            if raw in (None, ""):
                ClassGrade.objects.filter(course=course, student_id=int(sid), period="monthly", title=title).delete()
                continue
            try:
                score = Decimal(str(raw))
            except Exception:
                raise serializers.ValidationError({"scores": "نمره‌ها باید عدد باشند."})
            if not 0 <= score <= 20:
                raise serializers.ValidationError({"scores": "نمره‌ی ماهیانه بین ۰ تا ۲۰ باشد."})
            ClassGrade.objects.update_or_create(course=course, student_id=int(sid), period="monthly", title=title,
                                                defaults={"score": score, "max_score": 20, "date": timezone.localdate(), "recorded_by": request.user})
            saved += 1
        audit(request, "classbook.monthly", f"course:{course.pk} month:{jm} saved:{saved}")
        return Response({"ok": True, "saved": saved})
