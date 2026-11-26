"""اتصال سامانه‌های کلاس آنلاین هر مدرسه (BigBlueButton، Adobe Connect، Skyroom) توسط مدیر یا معاون همان مدرسه.

رمز سامانه هرگز در پایگاه داده نمی‌رود؛ فقط نام متغیر محیطی (secret_env) ذخیره می‌شود.
"""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit
from apps.core.models import audit
from apps.core.scope import org_in_scope

from .models import ExternalSystem

ROLES = ("super_admin", "school_admin", "school_deputy_edu")
KINDS = {"bbb", "adobe", "skyroom"}
KIND_LABEL = {"bbb": "BigBlueButton", "adobe": "Adobe Connect", "skyroom": "Skyroom"}


def _check(user, school):
    if user.role not in ROLES or not org_in_scope(user, school):
        raise PermissionDenied("اتصال سامانه‌ها را مدیر یا معاون آموزشی همان مدرسه تنظیم می‌کند.")


def _clean_or_400(x):
    """اعتبارسنجی مدل؛ خطای مدل (Django) به پیغام فارسی روی فیلد تبدیل می‌شود، نه خطای ۵۰۰."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    try:
        x.full_clean()
    except DjangoValidationError as e:
        if hasattr(e, "error_dict"):
            raise serializers.ValidationError({k: [str(m) for m in v] for k, v in e.message_dict.items()})
        raise serializers.ValidationError({"detail": e.messages})


def _apply_secrets(x, data):
    """رمز و رمز مدیر فقط در صورت وارد شدن عوض می‌شوند و رمزگذاری‌شده ذخیره می‌شوند."""
    if str(data.get("secret") or "").strip():
        x.set_secret(str(data["secret"]).strip())
    if str(data.get("admin_password") or "").strip():
        x.set_admin_password(str(data["admin_password"]).strip())
    if data.get("clear_secret") in (True, "true", "1", 1):
        x.secret_enc = ""
    if data.get("clear_admin_password") in (True, "true", "1", 1):
        x.admin_password_enc = ""


def _row(x):
    # the secret and the password are never sent back: only whether they are set
    return {"id": x.pk, "name": x.name, "kind": x.kind, "kind_label": x.get_kind_display(), "base_url": x.base_url,
            "secret_env": x.secret_env, "secret_set": bool(x.secret_enc), "admin_user": x.admin_user,
            "admin_password_set": bool(x.admin_password_enc), "is_active": x.is_active, "notes": x.notes,
            "last_check_ok": x.last_check_ok, "last_check_detail": x.last_check_detail}


class SchoolSystemListView(APIView):
    def get(self, request):
        sid = request.query_params.get("school")
        if not str(sid or "").isdigit():
            raise serializers.ValidationError({"school": "ابتدا مدرسه را انتخاب کنید."})
        school = OrgUnit.objects.filter(pk=int(sid), kind="school").first()
        if school is None:
            raise serializers.ValidationError({"school": "مدرسه‌ی انتخاب‌شده پیدا نشد."})
        _check(request.user, school)
        return Response([_row(x) for x in ExternalSystem.objects.filter(school=school).order_by("name")])

    def post(self, request):
        # the school comes from the page's address (?school=) or from the body
        sid = request.query_params.get("school") or request.data.get("school")
        if not str(sid or "").isdigit():
            raise serializers.ValidationError({"school": "ابتدا مدرسه را در کارتابل یا کادر مدرسه انتخاب کنید."})
        school = OrgUnit.objects.filter(pk=int(sid), kind="school").first()
        if school is None:
            raise serializers.ValidationError({"school": "مدرسه‌ی انتخاب‌شده پیدا نشد."})
        _check(request.user, school)
        kind = str(request.data.get("kind") or "")
        if kind not in KINDS:
            raise serializers.ValidationError({"kind": "نوع سامانه را انتخاب کنید (BigBlueButton، Adobe Connect یا Skyroom)."})
        # a school may have many connections: a repeated or empty name gets a number instead of an error
        base_name = (str(request.data.get("name") or "").strip() or KIND_LABEL.get(kind, kind))[:90]
        name, n = base_name, 1
        while ExternalSystem.objects.filter(school=school, name=name).exists():
            n += 1
            name = f"{base_name} ({n})"
        x = ExternalSystem(school=school, kind=kind, name=name[:100],
                           base_url=str(request.data.get("base_url") or "").strip(),
                           secret_env=str(request.data.get("secret_env") or "").strip(),
                           admin_user=str(request.data.get("admin_user") or "").strip()[:100],
                           notes=str(request.data.get("notes") or ""), is_active=bool(request.data.get("is_active", True)))
        _apply_secrets(x, request.data)
        _clean_or_400(x)
        x.save()
        audit(request, "integration.school.create", f"school:{school.pk} system:{x.pk}")
        return Response(_row(x), status=201)


class SchoolSystemItemView(APIView):
    def _get(self, request, pk):
        x = get_object_or_404(ExternalSystem.objects.select_related("school"), pk=pk, school__isnull=False)
        _check(request.user, x.school)
        return x

    def patch(self, request, pk):
        x = self._get(request, pk)
        for f in ("name", "base_url", "secret_env", "notes"):
            if f in request.data:
                setattr(x, f, str(request.data[f] or "").strip())
        if "is_active" in request.data:
            x.is_active = bool(request.data["is_active"])
        if "admin_user" in request.data:
            x.admin_user = str(request.data.get("admin_user") or "").strip()[:100]
        _apply_secrets(x, request.data)
        _clean_or_400(x)
        x.save()
        audit(request, "integration.school.edit", f"system:{x.pk}")
        return Response(_row(x))

    def delete(self, request, pk):
        x = self._get(request, pk)
        audit(request, "integration.school.delete", f"system:{x.pk}")
        x.delete()
        return Response(status=204)


class SchoolSystemTestView(APIView):
    """POST /api/school-systems/<pk>/test/ — تست ارتباط با سامانه‌ی کلاس آنلاین مدرسه (Adobe یا BigBlueButton)."""

    def post(self, request, pk):
        from django.utils import timezone
        x = get_object_or_404(ExternalSystem.objects.select_related("school"), pk=pk, school__isnull=False)
        _check(request.user, x.school)
        try:
            if x.kind == "adobe":
                from apps.liveclass import adobe
                detail = adobe.test_connection(x.base_url, x.admin_user, x.get_admin_password())
            elif x.kind == "bbb":
                from apps.liveclass.providers import BigBlueButton, _http_get
                secret = x.secret()
                if not (x.base_url and secret):
                    raise ValueError("آدرس یا رمز BigBlueButton تنظیم نشده است.")
                url = BigBlueButton()._signed("getMeetings", {}, x.base_url, secret)
                _http_get(url)
                detail = "ارتباط با BigBlueButton برقرار است."
            else:
                raise ValueError("تست ارتباط برای این نوع سامانه هنوز پیاده‌سازی نشده است.")
            ok, text = True, detail
        except Exception as e:  # noqa: BLE001 - show the reason to the manager, never the secret
            ok, text = False, str(e)[:200]
        x.last_check_at = timezone.now()
        x.last_check_ok = ok
        x.last_check_detail = text[:200]
        x.save(update_fields=["last_check_at", "last_check_ok", "last_check_detail"])
        audit(request, "integration.school.test", f"system:{x.pk} ok:{ok}")
        return Response({"ok": ok, "detail": text})

