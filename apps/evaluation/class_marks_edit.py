"""نمرات سرکلاسی قبلی: معلم درس خودش را مرور می‌کند و هر نمره یا عنوان/تاریخ/کل نمره‌ی یک گروه را ویرایش می‌کند.
فقط نمره‌های سرکلاسی (period=class) درس خود معلم؛ نمره‌ی قفل‌شده‌ی آزمون (نمره‌ی نهایی) اینجا تغییر نمی‌کند."""
from collections import OrderedDict
from datetime import date as _date
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.courses.models import Course
from apps.core.models import audit

from .models import ClassGrade


def _course(request, pk):
    from .classgrades import _course_for_write
    course = get_object_or_404(Course.objects.select_related("classroom", "org"), pk=pk)
    _course_for_write(request.user, course)
    return course


class PastClassMarksView(APIView):
    """GET ?course=ID — نمره‌های سرکلاسی درس، گروه‌بندی‌شده بر اساس عنوان، تاریخ و کل نمره."""

    def get(self, request):
        course = _course(request, request.query_params.get("course") or 0)
        groups = OrderedDict()
        qs = ClassGrade.objects.filter(course=course, period="class").select_related("student").order_by("-date", "title", "student__last_name")
        for g in qs:
            key = (g.title, g.date.isoformat(), str(g.max_score))
            grp = groups.setdefault(key, {"title": g.title, "date": g.date.isoformat(), "max_score": float(g.max_score), "rows": []})
            grp["rows"].append({"id": g.pk, "student": g.student_id, "name": g.student.get_full_name() or g.student.username,
                                "code": g.student.student_code or "", "score": float(g.score)})
        return Response({"course": course.title, "classroom": course.classroom.name if course.classroom_id else "",
                         "groups": list(groups.values())})


class EditClassMarksView(APIView):
    """POST {course, title, date, max_score, rows:[{id, score}], new_title?, new_date?, new_max?}
    نمره‌ها را در محدوده‌ی 0 تا کل نمره‌ی همان گروه ذخیره می‌کند؛ اگر new_* بدهید، عنوان/تاریخ/کل نمره‌ی گروه هم عوض می‌شود."""

    def post(self, request):
        course = _course(request, request.data.get("course") or 0)
        old_title = str(request.data.get("title") or "").strip()
        try:
            old_date = _date.fromisoformat(str(request.data.get("date") or ""))
            old_max = Decimal(str(request.data.get("max_score")))
        except (ValueError, ArithmeticError, TypeError):
            raise serializers.ValidationError({"detail": "تاریخ یا کل نمره‌ی گروه نامعتبر است."})
        if old_max <= 0 or old_max > 100:
            raise serializers.ValidationError({"max_score": "کل نمره باید بین ۰ و ۱۰۰ باشد."})
        group = ClassGrade.objects.filter(course=course, period="class", title=old_title, date=old_date, max_score=old_max)
        ids = {g.pk: g for g in group}
        bad = []
        for row in request.data.get("rows") or []:
            pk = int(row.get("id") or 0)
            if pk not in ids:
                continue
            try:
                score = Decimal(str(row.get("score")))
            except ArithmeticError:
                bad.append({"id": pk, "reason": "نمره باید عدد باشد."})
                continue
            if score < 0 or score > old_max:
                bad.append({"id": pk, "reason": f"نمره باید بین ۰ تا {old_max} باشد."})
                continue
            g = ids[pk]
            g.score = score
            g.save(update_fields=["score"])
        if bad:
            raise serializers.ValidationError({"rows": bad})
        new_title = str(request.data.get("new_title") or old_title).strip()[:200] or old_title
        new_date = request.data.get("new_date")
        new_date = _date.fromisoformat(new_date) if new_date else old_date
        new_max = Decimal(str(request.data.get("new_max") or old_max))
        if new_max <= 0 or new_max > 100:
            raise serializers.ValidationError({"new_max": "کل نمره باید بین ۰ و ۱۰۰ باشد."})
        if (new_title, new_date, new_max) != (old_title, old_date, old_max):
            for g in ids.values():
                g.title, g.date, g.max_score = new_title, new_date, new_max
                if g.score > new_max:
                    g.score = new_max
                g.save(update_fields=["title", "date", "max_score", "score"])
        audit(request, "classgrade.edit_group", f"course:{course.pk} group:{old_title}/{old_date}")
        return Response({"ok": True, "updated": len(ids)})
