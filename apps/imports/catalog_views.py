"""CSV / Excel / pasted-table import of the geography and the study-field catalogue (system admin and province admins).
Stateless: preview parses and validates; commit validates AGAIN and writes only the valid rows."""
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.academics.models import Level, StudyField
from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.persian import canonical
from apps.core.scope import org_in_scope

from .parsers import parse

Allowed = role_required(User.Role.SUPER_ADMIN, User.Role.PROVINCE_ADMIN)
KINDS = ("counties", "regions", "catalog_fields")
MAX_UPLOAD = 5 * 1024 * 1024


def _province(request):
    pid = request.data.get("province")
    province = get_object_or_404(OrgUnit, pk=pid or 0, kind="province")
    if not org_in_scope(request.user, province):
        raise PermissionDenied("استان خارج از محدوده شماست.")
    return province


def validate(kind, province, rows):
    out, seen_names, seen_codes = [], set(), set()
    if kind in ("counties", "regions"):
        wanted = "county" if kind == "counties" else "region"
        existing = {canonical(n) for n in OrgUnit.objects.filter(kind=wanted, path__startswith=province.path).values_list("name", flat=True)}
        db_codes = set(OrgUnit.objects.exclude(code=None).values_list("code", flat=True))
    else:
        existing = {canonical(n) for n in StudyField.objects.values_list("name", flat=True)}
        db_codes = set(StudyField.objects.exclude(code=None).values_list("code", flat=True))
        levels = {l.code for l in Level.objects.all()}
    for r in rows:
        v = {k: (val or "").strip() for k, val in r["values"].items()}
        errs = []
        if not v.get("name"):
            errs.append("نام لازم است")
        else:
            key = canonical(v["name"])
            if key in existing and kind != "catalog_fields":
                errs.append("این نام قبلاً در همین استان ثبت شده است")
            if key in seen_names:
                errs.append("تکراری در همین فایل")
            seen_names.add(key)
        code = v.get("code") or ""
        if kind == "catalog_fields" and not code:
            errs.append("کد رشته لازم است")
        if code:
            if code in db_codes:
                errs.append("این کد قبلاً ثبت شده است")
            if code in seen_codes:
                errs.append("کد تکراری در همین فایل")
            seen_codes.add(code)
        if kind == "catalog_fields" and v.get("level_code") and v["level_code"] not in levels:
            errs.append(f"مقطع با کد {v['level_code']} وجود ندارد")
        out.append({"n": r["n"], "values": v, "errors": errs, "status": "error" if errs else "ok"})
    return out


def commit(kind, province, rows):
    created, failed = 0, []
    for r in rows:
        if r["status"] != "ok":
            continue
        v = r["values"]
        try:
            with transaction.atomic():
                if kind == "catalog_fields":
                    lvl = Level.objects.filter(code=v["level_code"]).first() if v.get("level_code") else None
                    StudyField.objects.get_or_create(code=v["code"], defaults={"name": v["name"], "level": lvl})[0]
                else:
                    OrgUnit.objects.create(name=v["name"], kind="county" if kind == "counties" else "region", parent=province,
                                           code=v.get("code") or None)
            created += 1
        except (IntegrityError, Exception) as e:           # one bad row never blocks the others
            failed.append({"n": r["n"], "reason": str(e)[:120]})
    return created, failed


class CatalogPreviewView(APIView):
    permission_classes = [Allowed]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"

    def post(self, request):
        kind = request.data.get("kind", "")
        if kind not in KINDS:
            raise serializers.ValidationError({"kind": "نامعتبر"})
        province = _province(request) if kind != "catalog_fields" else None
        f, text = request.FILES.get("file"), request.data.get("text")
        if (f is None) == (text is None):
            raise serializers.ValidationError({"file": "یکی از «فایل» یا «متن چسبانده‌شده» را بفرستید."})
        if f is not None and f.size > MAX_UPLOAD:
            raise serializers.ValidationError({"file": "حجم فایل بیش از ۵ مگابایت است."})
        try:
            rows, unknown = parse(kind, name=f.name, data=f.read()) if f is not None else parse(kind, text=str(text)[:500_000])
        except ValueError as e:
            raise serializers.ValidationError({"file": str(e)})
        checked = validate(kind, province, rows)
        return Response({"kind": kind, "unknown_headers": unknown, "rows": checked,
                         "summary": {"total": len(checked), "ok": sum(1 for r in checked if r["status"] == "ok"),
                                     "error": sum(1 for r in checked if r["status"] == "error")}})


class CatalogCommitView(APIView):
    permission_classes = [Allowed]

    def post(self, request):
        kind = request.data.get("kind", "")
        if kind not in KINDS:
            raise serializers.ValidationError({"kind": "نامعتبر"})
        province = _province(request) if kind != "catalog_fields" else None
        raw = request.data.get("rows")
        if not isinstance(raw, list) or len(raw) > 2000:
            raise serializers.ValidationError({"rows": "فهرست نامعتبر است."})
        rows = [{"n": int(r.get("n", i + 1)), "values": {k: str(v)[:200] for k, v in (r.get("values") or {}).items()}}
                for i, r in enumerate(raw)]
        checked = validate(kind, province, rows)
        created, failed = commit(kind, province, checked)
        audit(request, "catalog.import", f"{kind} created={created}")
        return Response({"created": created, "failed": failed, "skipped": sum(1 for r in checked if r["status"] != "ok")})
