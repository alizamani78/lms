from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope

from .crypto import encrypt_secret
from .models import SmsGateway, SmsLog
from .providers import SmsError, send
from .recovery import normalize_mobile, mask

IsAdmin = role_required(*User.ADMIN_ROLES)


def _row(g):
    return {"id": g.pk, "name": g.name, "base_url": g.base_url, "auth_header": g.auth_header,
            "body_template": g.body_template, "sender": g.sender, "is_active": g.is_active,
            "has_secret": bool(g.auth_secret_enc),                         # the secret itself is never returned
            "last_test_at": g.last_test_at, "last_test_ok": g.last_test_ok}


class GatewayIn(serializers.Serializer):
    name = serializers.CharField(max_length=80)
    base_url = serializers.URLField(max_length=300)
    auth_header = serializers.CharField(max_length=40, required=False, default="Authorization")
    auth_secret = serializers.CharField(max_length=500, required=False, allow_blank=True, write_only=True)
    body_template = serializers.CharField(max_length=4096, required=False)
    sender = serializers.CharField(max_length=30, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False, default=False)

    def validate_base_url(self, v):
        if not v.startswith("https://"):
            raise serializers.ValidationError("آدرس پنل باید با https شروع شود.")
        return v


def _school(request, pk):
    school = get_object_or_404(OrgUnit, pk=pk, kind=OrgUnit.Kind.SCHOOL)
    if not org_in_scope(request.user, school):
        raise PermissionDenied()
    return school


class SchoolSmsView(APIView):
    """GET list / POST create the school's SMS panel. Only the school's principal or admins above it."""
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        school = _school(request, pk)
        return Response([_row(g) for g in SmsGateway.objects.filter(school=school).order_by("name")])

    def post(self, request, pk):
        school = _school(request, pk)
        s = GatewayIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        secret = d.pop("auth_secret", "")
        if d.get("is_active"):
            SmsGateway.objects.filter(school=school, is_active=True).update(is_active=False)
        g = SmsGateway(school=school, **d)
        if secret:
            g.auth_secret_enc = encrypt_secret(secret)
        g.save()
        audit(request, "sms.gateway.create", f"gateway:{g.pk}")
        return Response(_row(g), status=201)


class SchoolSmsDetailView(APIView):
    permission_classes = [IsAdmin]

    def _gw(self, request, pk, gid):
        school = _school(request, pk)
        return get_object_or_404(SmsGateway, pk=gid, school=school)

    def put(self, request, pk, gid):
        g = self._gw(request, pk, gid)
        s = GatewayIn(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        if "auth_secret" in d:
            secret = d.pop("auth_secret")
            if secret:
                g.auth_secret_enc = encrypt_secret(secret)
            else:
                g.auth_secret_enc = ""
        if d.get("is_active"):
            SmsGateway.objects.filter(school=g.school, is_active=True).exclude(pk=g.pk).update(is_active=False)
        for k, v in d.items():
            setattr(g, k, v)
        g.save()
        audit(request, "sms.gateway.update", f"gateway:{g.pk}")
        return Response(_row(g))

    def delete(self, request, pk, gid):
        g = self._gw(request, pk, gid)
        audit(request, "sms.gateway.delete", f"gateway:{g.pk}")
        g.delete()
        return Response(status=204)


class SchoolSmsTestView(APIView):
    """POST {mobile}: sends one test SMS through the panel (accepted/refused is recorded; the text has no secret)."""
    permission_classes = [IsAdmin]

    def post(self, request, pk, gid):
        g = get_object_or_404(SmsGateway, pk=gid, school=_school(request, pk))
        mobile = normalize_mobile(str(request.data.get("mobile", "")))
        if mobile is None:
            raise serializers.ValidationError({"mobile": "شماره موبایل معتبر نیست (مثال 09121234567)."})
        try:
            ok, detail = send(g, mobile, "پیامک آزمایشی سامانه آموزش رضوی.")
        except SmsError as e:
            ok, detail = False, str(e)[:100]
        g.last_test_at, g.last_test_ok = timezone.now(), ok
        g.save(update_fields=["last_test_at", "last_test_ok"])
        SmsLog.objects.create(gateway=g, to_masked=mask(mobile), purpose="test", accepted=ok, detail=detail[:120])
        audit(request, "sms.gateway.test", f"gateway:{g.pk} ok={ok}")
        return Response({"accepted": ok, "detail": detail})


class SchoolSmsLogView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        school = _school(request, pk)
        rows = SmsLog.objects.filter(gateway__school=school).order_by("-created_at")[:200]
        return Response([{"at": r.created_at, "to": r.to_masked, "purpose": r.purpose,
                          "accepted": r.accepted, "detail": r.detail} for r in rows])
