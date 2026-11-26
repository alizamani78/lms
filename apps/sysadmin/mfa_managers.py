from rest_framework.permissions import BasePermission
"""تأیید دو مرحله‌ای مدیران: مدیر سیستم برای هر مدیر (مدیر کل، استان، شهرستان، منطقه، مدرسه، معاونان) فعال یا غیرفعال می‌کند."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.mfa import encrypt, new_secret, provisioning_uri
from apps.accounts.models import SchoolMembership, TOTPDevice, User
from apps.core.models import audit

from apps.core.permissions import role_required

SuperOnly = role_required(User.Role.SUPER_ADMIN)


class SchoolOrSuperAdmin(BasePermission):
    """مدیر سیستم همه‌ی مدیران را؛ مدیر مدرسه فقط مدیران و کادر مدرسه‌ی خودش را می‌تواند تغییر دهد."""

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.role in ("super_admin", "school_admin"))

    def has_object_permission(self, request, view, obj):
        u = request.user
        if u.role == "super_admin":
            return True
        from apps.core.scope import org_in_scope
        return bool(getattr(obj, "org_id", None) and org_in_scope(u, obj.org))

MANAGER_ROLES = ["super_admin", "province_admin", "county_admin", "region_admin", "school_admin",
                 "school_deputy_edu", "school_deputy_exec", "school_deputy_culture"]


def _school_of(u):
    m = SchoolMembership.objects.filter(user=u).select_related("school").first()
    return m.school.name if m and m.school else ""


class ManagerMfaListView(APIView):
    """GET — مدیران سامانه با وضعیت تأیید دو مرحله‌ای."""
    permission_classes = [SchoolOrSuperAdmin]

    def get(self, request):
        users = User.objects.filter(role__in=MANAGER_ROLES, is_active=True).order_by("role", "last_name", "username")
        if request.user.role != "super_admin":
            users = users.filter(org=request.user.org)
        enabled = set(TOTPDevice.objects.filter(confirmed=True, user__in=users).values_list("user_id", flat=True))
        return Response([{"id": u.pk, "name": (u.get_full_name() or u.username), "username": u.username,
                          "role": u.role, "role_label": u.get_role_display(), "school": _school_of(u),
                          "mfa": u.pk in enabled} for u in users])


class ManagerMfaToggleView(APIView):
    """POST {id, enabled} — فعال (کلید تازه، یک‌بار نمایش) یا غیرفعال کردن تأیید دو مرحله‌ای یک مدیر."""
    permission_classes = [SchoolOrSuperAdmin]

    def post(self, request):
        target = get_object_or_404(User, pk=request.data.get("id") or 0, role__in=MANAGER_ROLES)
        self.check_object_permissions(request, target)
        want = request.data.get("enabled")
        if isinstance(want, str):
            want = want.lower() in ("1", "true", "yes", "on")
        if not isinstance(want, bool):
            raise serializers.ValidationError({"enabled": "وضعیت را مشخص کنید."})
        if want:
            if TOTPDevice.objects.filter(user=target, confirmed=True).exists():
                return Response({"ok": True, "mfa": True, "detail": "تأیید دو مرحله‌ای این مدیر از قبل فعال است."})
            secret = new_secret()
            TOTPDevice.objects.update_or_create(user=target, defaults={"secret_enc": encrypt(secret), "confirmed": True, "last_step": 0})
            audit(request, "mfa.manager.enable", f"user:{target.pk}")
            return Response({"ok": True, "mfa": True, "secret": secret, "uri": provisioning_uri(target, secret),
                             "detail": "این کلید فقط همین یک بار نمایش داده می‌شود؛ در برنامه Authenticator وارد کنید."})
        TOTPDevice.objects.filter(user=target).delete()
        audit(request, "mfa.manager.disable", f"user:{target.pk}")
        return Response({"ok": True, "mfa": False, "detail": "تأیید دو مرحله‌ای این مدیر غیرفعال شد."})
