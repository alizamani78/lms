from datetime import timedelta

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit
from apps.core.scope import org_scope_q

from apps.core.permissions import role_required
from apps.exams.authoring import staff_courses
from apps.integrations.models import ExternalSystem

from .models import ClassSession
from .providers import PROVIDERS


class JoinView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "join"

    def post(self, request, pk):
        u = request.user
        session = get_object_or_404(visible_sessions(u).select_related("system"), pk=pk)
        if session.remote_status in ("pending", "failed"):
            return Response({"detail": "اتاق کلاس هنوز آماده نیست."}, status=409)
        now = timezone.now()
        if u.role == User.Role.STUDENT and not (session.starts_at <= now <= session.ends_at):
            raise PermissionDenied("کلاس در این زمان فعال نیست.")
        provider = PROVIDERS.get(session.provider)
        if provider is None:
            return Response({"detail": "ارائه‌دهنده پشتیبانی نمی‌شود."}, status=501)
        moderator = u.role != User.Role.STUDENT
        if not moderator and session.provider == "bbb":
            try:
                running = provider.is_running(session)
            except RuntimeError:
                return Response({"detail": "سرویس کلاس آنلاین در دسترس نیست."}, status=503)
            if not running:
                return Response({"detail": "کلاس هنوز آغاز نشده است. لطفاً منتظر بمانید تا دبیر یا مدیر وارد جلسه شود."}, status=409)
        try:
            url = provider.join_url(session, u, moderator)
        except RuntimeError:
            return Response({"detail": "سرویس کلاس آنلاین در دسترس نیست."}, status=503)
        audit(request, "live.join", f"session:{session.pk}")
        return Response({"url": url})


def visible_sessions(user):
    qs = ClassSession.objects.select_related("course")
    if user.role == User.Role.STUDENT:
        return qs.filter(course__enrollments__student=user)
    if user.role == User.Role.TEACHER:
        return qs.filter(course__teacher=user)
    if user.role in User.ADMIN_ROLES:
        return qs.filter(org_scope_q(user, "course__org"))
    return qs.none()


class LiveListView(APIView):
    """Sessions from 30 days ago onward (own classes only)."""

    def get(self, request):
        since = timezone.now() - timedelta(days=30)
        u = request.user
        from apps.core.scope import kartabl_filter
        rows = visible_sessions(u).filter(ends_at__gte=since, **kartabl_filter(request, "course__org_id")).order_by("starts_at")[:100]
        staff = u.role in User.STAFF_ROLES
        return Response([{
            "id": s.pk, "title": s.title, "course": s.course.title, "provider": s.provider,
            "starts_at": s.starts_at, "ends_at": s.ends_at, "remote_status": s.remote_status,
            "joinable": s.remote_status in ("manual", "ready"),
            **({"attendance_status": s.attendance_status, "last_error": s.last_error} if staff else {})}
            for s in rows])


class SessionIn(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    provider = serializers.ChoiceField(choices=[c[0] for c in ClassSession.Provider.choices], default="bbb")
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    auto_create = serializers.BooleanField(default=True)
    external_id = serializers.CharField(required=False, allow_blank=True, max_length=100)
    min_presence_percent = serializers.IntegerField(min_value=1, max_value=100, default=50)
    student_perms = serializers.DictField(child=serializers.BooleanField(), required=False, default=dict)

    def validate_student_perms(self, v):
        bad = [k for k in v if k not in ClassSession.PERM_KEYS]
        if bad:
            raise serializers.ValidationError(f"امکان ناشناخته: {', '.join(bad)}")
        return {k: bool(v.get(k, False)) for k in ClassSession.PERM_KEYS}

    def validate(self, d):
        if d["ends_at"] <= d["starts_at"]:
            raise serializers.ValidationError({"ends_at": "پایان باید بعد از شروع باشد."})
        if not d["auto_create"] and not d.get("external_id"):
            raise serializers.ValidationError({"external_id": "برای ساخت دستی، شناسه جلسه لازم است."})
        if d["auto_create"] and d["provider"] not in PROVIDERS:
            raise serializers.ValidationError({"provider": "ساخت خودکار برای این سرویس هنوز پشتیبانی نمی‌شود."})
        return d


class SessionCreateView(APIView):
    """POST /api/courses/<id>/sessions/ — the system (BBB/Adobe/Skyroom connection) is chosen by the platform admin."""
    permission_classes = [role_required(*User.STAFF_ROLES)]

    def post(self, request, course_id):
        course = get_object_or_404(staff_courses(request.user), pk=course_id)
        s = SessionIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        # سامانه‌ی خود مدرسه اولویت دارد؛ اگر نبود، سامانه‌ی سراسری
        system = (ExternalSystem.objects.filter(kind=d["provider"], is_active=True, school=course.org).order_by("id").first()
                  or ExternalSystem.objects.filter(kind=d["provider"], is_active=True, school__isnull=True).order_by("id").first())
        if system is None and not (d["provider"] == "bbb" and settings.BBB_URL):
            return Response({"detail": "هنوز سامانه‌ای برای این سرویس توسط مدیر سامانه تعریف نشده است."}, status=400)
        session = ClassSession.objects.create(course=course, system=system, **d)
        audit(request, "live.create", f"session:{session.pk}")
        return Response({"id": session.pk, "remote_status": session.remote_status}, status=201)
