"""برگ ریز نمرات: پیوند کوتاه‌مدت چاپ (در تب جدید)، و فهرست درس‌های قابل چاپ برای دبیر و مدیر."""
from django.core import signing
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

from . import grade_sheet as G

ADMIN = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec")


def _can(user, course):
    if user.role == "teacher" and course.teacher_id == user.pk:
        return True
    return user.role in ADMIN and org_in_scope(user, course.org)


class GradeSheetOptionsView(APIView):
    def get(self, request):
        qs = Course.objects.select_related("org", "classroom")
        if request.user.role == "teacher":
            qs = qs.filter(teacher=request.user)
        elif request.user.role not in ADMIN:
            raise PermissionDenied("برگ ریز نمرات برای دبیر و مدیر است.")
        rows = [c for c in qs.order_by("title")[:600]
                if request.user.role in ("super_admin", "teacher") or org_in_scope(request.user, c.org)]
        return Response([{"id": c.pk, "label": f"{c.title} — {c.classroom.name if c.classroom_id else ''}"} for c in rows[:300]])


class GradeSheetLinkView(APIView):
    def get(self, request):
        course = get_object_or_404(Course.objects.select_related("org"), pk=request.query_params.get("course") or 0)
        if not _can(request.user, course):
            raise PermissionDenied("این برگ ریز را فقط دبیر درس یا مسئول مدرسه می‌تواند چاپ کند.")
        token = signing.dumps({"c": course.pk, "t": request.query_params.get("term") or "اول"}, salt="grade-sheet")
        audit(request, "grade.sheet.link", f"course:{course.pk}")
        return Response({"url": f"/api/grade-sheet/{token}/"})


class GradeSheetPublicView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        try:
            p = signing.loads(token, salt="grade-sheet", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی برگ ریز باز کنید.", status=403,
                                content_type="text/plain; charset=utf-8")
        course = get_object_or_404(Course.objects.select_related("org", "classroom__academic_year", "classroom__study_field",
                                                                 "subject", "teacher"), pk=p["c"])
        d = G.build_grade_sheet(course, course.classroom, term=p.get("t") or "اول")
        resp = HttpResponse(G.render_html(d), content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp
