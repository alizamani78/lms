"""System tools: everything an administrator used to need the Django admin or the server console for.
Super admin only; every action is written to the audit log; secrets are shown once and never stored in plain text."""
import io
import sys

import django
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicYear
from apps.accounts.mfa import encrypt, new_secret, provisioning_uri
from apps.accounts.models import TOTPDevice, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.integrations.health import check as check_external
from apps.integrations.models import ExternalSystem
from apps.sms.crypto import encrypt_secret
from apps.sms.models import SmsGateway

SuperOnly = role_required(User.Role.SUPER_ADMIN)
SEEDS = {"catalog": "seed_catalog", "provinces": "seed_provinces", "counties": "seed_counties"}


def _user(request):
    username = str(request.data.get("username", "")).strip()[:150]
    return get_object_or_404(User, username=username)


class OverviewView(APIView):
    permission_classes = [SuperOnly]

    def get(self, request):
        no_mfa = [u.username for u in User.objects.filter(role="super_admin", is_active=True)
                  if not TOTPDevice.objects.filter(user=u, confirmed=True).exists()]
        year = AcademicYear.objects.filter(is_current=True).first()
        from apps.files.views import storage_root
        root = storage_root()
        return Response({
            "debug": bool(settings.DEBUG), "django": django.get_version(), "python": sys.version.split()[0],
            "database": connection.vendor, "cache": settings.CACHES["default"]["BACKEND"].rsplit(".", 1)[-1],
            "celery_eager": bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)),
            "private_media_ready": root.is_dir() or not root.exists(),
            "mfa_enforced": bool(getattr(settings, "MFA_ENFORCE", False)),
            "super_admins_without_mfa": no_mfa, "current_year": year.name if year else None})


class SeedView(APIView):
    permission_classes = [SuperOnly]

    def post(self, request):
        name = request.data.get("name")
        if name not in SEEDS:
            raise serializers.ValidationError({"name": "نامعتبر"})
        out = io.StringIO()
        try:
            call_command(SEEDS[name], stdout=out, stderr=out)
        except Exception as e:                               # the command prints a clear Persian message
            return Response({"ok": False, "detail": str(e)[:300]}, status=400)
        audit(request, "tools.seed", name)
        return Response({"ok": True, "detail": out.getvalue().strip()[:400]})


class CurrentYearView(APIView):
    permission_classes = [SuperOnly]

    def post(self, request):
        year = get_object_or_404(AcademicYear, pk=request.data.get("id") or 0)
        with transaction.atomic():
            AcademicYear.objects.exclude(pk=year.pk).update(is_current=False)
            year.is_current = True
            year.save(update_fields=["is_current"])
        cache.clear()                                         # ranks and policy caches depend on the current year
        audit(request, "tools.current_year", f"year:{year.pk}")
        return Response({"current": year.name})


class MfaToolView(APIView):
    """action=enroll: creates a CONFIRMED authenticator for the user and shows the secret once.
       action=reset: removes the authenticator (the user must be enrolled again)."""
    permission_classes = [SuperOnly]

    def post(self, request):
        user = _user(request)
        action = request.data.get("action")
        if action == "reset":
            TOTPDevice.objects.filter(user=user).delete()
            audit(request, "tools.mfa_reset", f"user:{user.pk}")
            return Response({"detail": "تأیید دو مرحله‌ای حذف شد."})
        if action == "enroll":
            secret = new_secret()
            TOTPDevice.objects.update_or_create(user=user, defaults={"secret_enc": encrypt(secret), "confirmed": True, "last_step": 0})
            audit(request, "tools.mfa_enroll", f"user:{user.pk}")
            return Response({"secret": secret, "uri": provisioning_uri(user, secret),
                             "detail": "این کلید فقط همین یک بار نمایش داده می‌شود؛ در برنامه Authenticator وارد کنید."})
        raise serializers.ValidationError({"action": "نامعتبر"})


class UnlockView(APIView):
    permission_classes = [SuperOnly]

    def post(self, request):
        user = _user(request)
        for key in (f"loginfail:{user.username[:40]}", f"adminfail:{user.username.lower()[:40]}",
                    f"sms-cool:{user.pk}", f"sms-hour:{user.pk}"):
            cache.delete(key)
        audit(request, "tools.unlock", f"user:{user.pk}")
        return Response({"detail": "قفل حساب برداشته شد."})


class SmsSecretView(APIView):
    """Set (or clear) a school's SMS panel key. The key is encrypted at once and never returned."""
    permission_classes = [SuperOnly]

    def post(self, request):
        gw = get_object_or_404(SmsGateway, pk=request.data.get("gateway") or 0)
        secret = str(request.data.get("secret", ""))[:500]
        gw.auth_secret_enc = encrypt_secret(secret) if secret else ""
        gw.save(update_fields=["auth_secret_enc"])
        audit(request, "tools.sms_secret", f"gateway:{gw.pk} set={bool(secret)}")
        return Response({"detail": "کلید ذخیره شد." if secret else "کلید پاک شد."})


class ExternalCheckView(APIView):
    permission_classes = [SuperOnly]

    def post(self, request):
        system = get_object_or_404(ExternalSystem, pk=request.data.get("id") or 0)
        ok, detail = check_external(system)
        audit(request, "tools.external_check", f"system:{system.pk} ok={ok}")
        return Response({"ok": ok, "detail": detail})


# ---------------------------------------------------------------------------------------------- 2FA by user search
from django.db.models import Q  # noqa: E402

from apps.core.scope import org_in_scope, scope_q  # noqa: E402

IsAdminRole = role_required(*User.ADMIN_ROLES)


def _manageable(actor, target):
    """Super admin: anyone. Other admins: only users of lower rank inside their scope."""
    if actor.role == User.Role.SUPER_ADMIN:
        return True
    return (not target.is_superuser and User.RANK[target.role] > User.RANK[actor.role]
            and target.org is not None and org_in_scope(actor, target.org))


class MfaUserSearchView(APIView):
    """GET ?q=<national id | username | name | student code> -> up to 20 users with their authenticator status."""
    permission_classes = [IsAdminRole]

    def get(self, request):
        q = request.query_params.get("q", "").strip()[:60]
        if len(q) < 2:
            return Response([])
        qs = User.objects.select_related("org").filter(
            Q(username__icontains=q) | Q(national_id__startswith=q) | Q(student_code__startswith=q)
            | Q(first_name__icontains=q) | Q(last_name__icontains=q))
        if request.user.role != User.Role.SUPER_ADMIN:
            qs = qs.filter(scope_q(request.user, "org__path"), is_superuser=False,
                           role__in=[r for r, v in User.RANK.items() if v > User.RANK[request.user.role]])
        confirmed = set(TOTPDevice.objects.filter(confirmed=True).values_list("user_id", flat=True))
        return Response([{"id": u.pk, "username": u.username, "name": u.get_full_name() or u.username,
                          "national_id": u.national_id or "", "role": u.get_role_display(), "org": u.org.name if u.org_id else "",
                          "mfa": u.pk in confirmed} for u in qs.order_by("last_name", "username")[:20]])


class MfaUserActionView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request):
        target = get_object_or_404(User, pk=request.data.get("id") or 0)
        if not _manageable(request.user, target):
            return Response(status=404)
        action = request.data.get("action")
        if action == "reset":
            TOTPDevice.objects.filter(user=target).delete()
            audit(request, "mfa.reset", f"user:{target.pk}")
            return Response({"detail": "تأیید دو مرحله‌ای غیرفعال شد."})
        if action == "enroll":
            secret = new_secret()
            TOTPDevice.objects.update_or_create(user=target, defaults={"secret_enc": encrypt(secret), "confirmed": True, "last_step": 0})
            audit(request, "mfa.enroll", f"user:{target.pk}")
            return Response({"secret": secret, "uri": provisioning_uri(target, secret),
                             "detail": "فعال شد. QR code و کلید فقط همین یک بار نمایش داده می‌شود."})
        raise serializers.ValidationError({"action": "نامعتبر"})
