"""شماره‌ی پشتیبانی آزمون و راهنمای پیش‌فرض آزمون‌های یک مدرسه: مدیر همان مدرسه (و مدیر سیستم) تعیین می‌کند."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, SchoolProfile
from apps.core.models import audit
from apps.core.scope import org_in_scope


def _check(request, school):
    u = request.user
    if u.role == "super_admin":
        return
    if u.role != "school_admin" or not org_in_scope(u, school):
        raise PermissionDenied("راهنمای آزمون را مدیر همان مدرسه تعیین می‌کند.")


class SchoolExamGuideView(APIView):
    """GET ?school=ID — شماره‌ی پشتیبانی و متن راهنمای آزمون مدرسه.
    PATCH {school, exam_support, exam_guide} — ذخیره (فقط مدیر همان مدرسه یا مدیر سیستم)."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request, school)
        prof = SchoolProfile.objects.filter(school=school).first()
        return Response({"exam_support": prof.exam_support if prof else "", "exam_guide": prof.exam_guide if prof else ""})

    def patch(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _check(request, school)
        prof, _ = SchoolProfile.objects.get_or_create(school=school)
        if "exam_support" in request.data:
            prof.exam_support = str(request.data.get("exam_support") or "").strip()[:200]
        if "exam_guide" in request.data:
            text = str(request.data.get("exam_guide") or "").strip()
            if len(text) > 20000:
                raise serializers.ValidationError({"exam_guide": "متن راهنما بیش از حد طولانی است."})
            prof.exam_guide = text
        prof.save(update_fields=["exam_support", "exam_guide"])
        audit(request, "exam.school_guide", f"school:{school.pk}")
        return Response({"ok": True})
