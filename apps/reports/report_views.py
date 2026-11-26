import csv
import io

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit
from apps.core.models import audit
from apps.core.persian import normalize_text
from apps.core.scope import org_in_scope, scope_q

from .catalog import REPORTS, available


def _safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


class ReportCatalogView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report"

    def get(self, request):
        return Response(available(request.user))


class ScopeOrgsView(APIView):
    """Organisations the caller may choose (provinces, counties, regions, schools), filtered by the level above:
    ?kind=school&province=<id>&region=<id>&county=<id>&q=<text>. Used by every province -> region -> school selector."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report"

    def get(self, request):
        p = request.query_params
        kind = p.get("kind", "school")
        if kind not in ("province", "county", "region", "school"):
            raise serializers.ValidationError({"kind": "نامعتبر"})
        qs = OrgUnit.objects.filter(kind=kind).filter(scope_q(request.user, "path")).select_related("parent")
        for key in ("province", "county", "region"):
            if p.get(key, "").isdigit():
                unit = OrgUnit.objects.filter(pk=int(p[key])).first()
                qs = qs.filter(path__startswith=unit.path) if unit else qs.none()
        q = p.get("q", "").strip()[:60]
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        return Response([{"id": o.pk, "name": (f"{o.name} — {o.parent.name}" if kind == "school" and o.parent_id else o.name)}
                         for o in qs.order_by("name")[:300]])


class ReportRunView(APIView):
    """GET /api/reports/run/<key>/?org=<id>&classroom=<id>&format=json|csv"""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report"

    def get(self, request, key):
        spec = REPORTS.get(key)
        if spec is None or request.user.role not in spec["roles"]:
            raise PermissionDenied()
        u, p = request.user, request.query_params
        org = None
        if p.get("org", "").isdigit():
            org = get_object_or_404(OrgUnit, pk=int(p["org"]))
            if not org_in_scope(u, org):
                return Response(status=404)
        elif u.role in ("school_admin", "teacher") and spec["scope"] != "district":
            org = u.school_memberships.order_by("id").values_list("school", flat=True).first()
            org = OrgUnit.objects.filter(pk=org).first() if org else None
        elif spec["scope"] == "district":
            org = None if u.role == "super_admin" else (OrgUnit.objects.filter(pk=u.org_id).first() if u.org_id else None)
        if spec["scope"] == "teacher":
            org = None
        elif org is None and u.role == "teacher":
            org = None
        classroom = None
        if p.get("classroom", "").isdigit():
            classroom = ClassRoom.objects.filter(pk=int(p["classroom"])).first()
            if classroom is None or org is None or classroom.school_id != org.pk:
                return Response(status=404)
        from .catalog import FILTER_KEYS
        kw = {"classroom": classroom, "date_from": p.get("from"), "date_to": p.get("to"),
              "filters": {k: p.get(k) for k in FILTER_KEYS if p.get(k)}, "group": p.get("group")}
        try:
            data = spec["fn"](u, org, **kw)
        except ValueError as e:
            raise serializers.ValidationError({"org": str(e)})
        audit(request, "report.run", f"{key} org={getattr(org, 'pk', None)}")
        if p.get("format") == "csv":
            buf = io.StringIO()
            buf.write("\ufeff")
            w = csv.writer(buf)
            w.writerow([label for _, label in data["columns"]])
            for r in data["rows"]:
                w.writerow([_safe(normalize_text(r.get(k, "")) if isinstance(r.get(k, ""), str) else r.get(k, "")) for k, _ in data["columns"]])
            resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{key}.csv"'
            resp["Cache-Control"] = "private, no-store"
            return resp
        return Response({"key": key, "label": spec["label"], "columns": [{"key": k, "label": l} for k, l in data["columns"]],
                         "rows": data["rows"], "totals": data["totals"],
                         "org": org.name if org else "", "classroom": classroom.name if classroom else ""})
