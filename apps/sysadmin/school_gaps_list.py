"""فهرست کمبودهای مدرسه با صفحه‌بندی، جستجو و فیلتر: تعداد در هر صفحه ۲۰ / ۵۰ / ۱۰۰ و شماره‌ی صفحه‌ها."""
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit, SchoolMembership, User
from apps.courses.models import Course

from .school_gaps import _school, _name

SIZES = (20, 50, 100)


def _page(qs, page, size, mapper):
    size = size if size in SIZES else 20
    pg = Paginator(qs, size)
    try:
        p = pg.page(max(1, int(page or 1)))
    except (ValueError, Exception):
        p = pg.page(pg.num_pages or 1) if pg.num_pages else None
    rows = [mapper(x) for x in (p.object_list if p else [])]
    return {"count": pg.count, "page": p.number if p else 1, "pages": pg.num_pages or 1, "size": size,
            "results": rows, "page_numbers": list(pg.page_range)}


def _words(qs, text, fields):
    for w in (text or "").strip()[:80].split():
        cond = Q()
        for f in fields:
            cond |= Q(**{f + "__icontains": w})
        qs = qs.filter(cond)
    return qs


class SchoolGapsListView(APIView):
    """GET ?school=ID&list=students|teachers|classes|courses&page=1&size=20&q=… — یک فهرست کمبود، صفحه‌بندی‌شده."""

    def get(self, request):
        school = _school(request, request.query_params.get("school"))
        kind = request.query_params.get("list") or "students"
        q = request.query_params.get("q") or ""
        page, size = request.query_params.get("page"), int(request.query_params.get("size") or 20)
        if kind == "students":
            in_class = User.objects.filter(role="student", classrooms__in=ClassRoom.objects.filter(school=school, academic_year__is_current=True)).values("pk")
            qs = User.objects.filter(role="student", org=school).exclude(pk__in=in_class).order_by("last_name", "first_name", "id")
            qs = _words(qs, q, ["first_name", "last_name", "student_code", "national_id"])
            return Response(_page(qs, page, size, lambda s: {"id": s.pk, "name": _name(s), "code": s.student_code or "", "national": s.national_id or ""}))
        if kind == "teachers":
            ids = SchoolMembership.objects.filter(school=school, user__role="teacher").values_list("user_id", flat=True)
            taught = Course.objects.filter(org=school, teacher__isnull=False).values("teacher_id")
            qs = User.objects.filter(pk__in=ids).exclude(pk__in=taught).order_by("last_name", "first_name", "id")
            qs = _words(qs, q, ["first_name", "last_name", "national_id", "username"])
            return Response(_page(qs, page, size, lambda t: {"id": t.pk, "name": _name(t), "national": t.national_id or ""}))
        if kind == "classes":
            with_courses = Course.objects.filter(org=school).values("classroom_id")
            qs = ClassRoom.objects.filter(school=school, academic_year__is_current=True).exclude(pk__in=with_courses).select_related("grade", "study_field").order_by("grade__name", "name")
            qs = _words(qs, q, ["name", "grade__name", "study_field__name"])
            return Response(_page(qs, page, size, lambda c: {"id": c.pk, "label": f"{c.grade.name} — {c.name}", "grade": c.grade.name,
                                                             "field": c.study_field.name if c.study_field_id else ""}))
        if kind == "courses":
            qs = Course.objects.filter(org=school, teacher__isnull=True).select_related("classroom", "subject").order_by("title", "id")
            qs = _words(qs, q, ["title", "classroom__name", "subject__name"])
            return Response(_page(qs, page, size, lambda c: {"id": c.pk, "title": c.title, "class": c.classroom.name if c.classroom_id else ""}))
        return Response({"count": 0, "page": 1, "pages": 1, "size": size, "results": [], "page_numbers": [1]})
