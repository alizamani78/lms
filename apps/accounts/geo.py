"""Cascading address: country (default Iran) -> province -> county -> region. Lists are read from OrgUnit."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.persian import canonical, normalize_text
from apps.core.scope import org_in_scope

from .models import OrgUnit

PROVINCES = ["آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز", "ایلام", "بوشهر", "تهران",
             "چهارمحال و بختیاری", "خراسان جنوبی", "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
             "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان", "کرمان", "کرمانشاه", "کهگیلویه و بویراحمد",
             "گلستان", "گیلان", "لرستان", "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد"]


class GeoView(APIView):
    """GET /api/geo/            -> provinces (cascade step 1)
       GET /api/geo/?parent=<id> -> children of a province/county (counties or regions)"""

    def get(self, request):
        parent = request.query_params.get("parent")
        if parent and parent.isdigit():
            qs = OrgUnit.objects.filter(parent_id=int(parent), kind__in=["county", "region"])
        else:
            qs = OrgUnit.objects.filter(kind="province", parent=None)
        return Response([{"id": o.pk, "name": o.name, "code": o.code or "", "kind": o.kind}
                         for o in qs.order_by("name")[:800]])




# ---------------------------------------------------------------- editable counties and regions
from rest_framework import serializers  # noqa: E402
from rest_framework.exceptions import PermissionDenied  # noqa: E402
from rest_framework.views import APIView as _APIView  # noqa: E402

from apps.core.models import audit  # noqa: E402
from apps.core.permissions import role_required  # noqa: E402
from apps.core.scope import org_in_scope  # noqa: E402

from .models import User  # noqa: E402

GEO_ADMIN = role_required(*User.ADMIN_ROLES)


class CountyIn(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    code = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    province = serializers.IntegerField()


class RegionIn(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    code = serializers.CharField(max_length=20, required=False, allow_blank=True, allow_null=True)
    parent = serializers.IntegerField()      # a county or a province


def _unit_row(o):
    return {"id": o.pk, "name": o.name, "code": o.code or "", "kind": o.kind, "parent": o.parent_id}


class CountyListView(_APIView):
    """GET /api/geo/counties/?province=<id>  |  POST (admins: inside their province scope; super admins: any)."""
    permission_classes = [GEO_ADMIN]

    def get(self, request):
        qs = OrgUnit.objects.filter(kind="county")
        if request.query_params.get("province", "").isdigit():
            qs = qs.filter(parent_id=int(request.query_params["province"]))
        return Response([_unit_row(o) for o in qs.order_by("name")[:1000]])

    def post(self, request):
        s = CountyIn(data=request.data)
        s.is_valid(raise_exception=True)
        prov = OrgUnit.objects.filter(pk=s.validated_data["province"], kind="province").first()
        if prov is None or not org_in_scope(request.user, prov):
            raise PermissionDenied("استان خارج از محدوده شماست.")
        try:
            o = OrgUnit.objects.create(name=s.validated_data["name"].strip(), kind="county", parent=prov,
                                       code=(s.validated_data.get("code") or None))
        except Exception as e:
            raise serializers.ValidationError({"code": "کد تکراری است." if "unique" in str(e).lower() else str(e)})
        audit(request, "geo.county.create", f"county:{o.pk}")
        return Response(_unit_row(o), status=201)


class CountyDetailView(_APIView):
    permission_classes = [GEO_ADMIN]

    def _get(self, request, pk):
        from django.shortcuts import get_object_or_404
        o = get_object_or_404(OrgUnit, pk=pk, kind="county")
        if not org_in_scope(request.user, o):
            raise PermissionDenied()
        return o

    def patch(self, request, pk):
        o = self._get(request, pk)
        name = request.data.get("name")
        if name is not None:
            if not str(name).strip():
                raise serializers.ValidationError({"name": "نام لازم است."})
            o.name = str(name).strip()[:150]
        if "code" in request.data:
            o.code = (str(request.data.get("code") or "").strip() or None)
        o.save()
        audit(request, "geo.county.update", f"county:{o.pk}")
        return Response(_unit_row(o))

    def delete(self, request, pk):
        o = self._get(request, pk)
        if o.children.exists() or o.school_profiles.exists():
            raise serializers.ValidationError({"detail": "این شهرستان مدرسه یا ناحیه دارد و قابل حذف نیست."})
        audit(request, "geo.county.delete", f"county:{o.pk}")
        o.delete()
        return Response(status=204)


class RegionListView(_APIView):
    """GET /api/geo/regions/?county=<id>|?province=<id>  |  POST (admins inside scope). Regions are separate from the address."""
    permission_classes = [GEO_ADMIN]

    def get(self, request):
        qs = OrgUnit.objects.filter(kind="region")
        p = request.query_params
        if p.get("county", "").isdigit():
            qs = qs.filter(parent_id=int(p["county"]))
        elif p.get("province", "").isdigit():
            prov = OrgUnit.objects.filter(pk=int(p["province"]), kind="province").first()
            qs = qs.filter(path__startswith=prov.path) if prov else qs.none()
        return Response([_unit_row(o) for o in qs.order_by("name")[:1000]])

    def post(self, request):
        s = RegionIn(data=request.data)
        s.is_valid(raise_exception=True)
        parent = OrgUnit.objects.filter(pk=s.validated_data["parent"], kind__in=["county", "province"]).first()
        if parent is None or not org_in_scope(request.user, parent):
            raise PermissionDenied("واحد بالاتر خارج از محدوده شماست.")
        o = OrgUnit.objects.create(name=s.validated_data["name"].strip(), kind="region", parent=parent,
                                   code=(s.validated_data.get("code") or None))
        audit(request, "geo.region.create", f"region:{o.pk}")
        return Response(_unit_row(o), status=201)


class RegionDetailView(CountyDetailView):
    def _get(self, request, pk):
        from django.shortcuts import get_object_or_404
        o = get_object_or_404(OrgUnit, pk=pk, kind="region")
        if not org_in_scope(request.user, o):
            raise PermissionDenied()
        return o

    def delete(self, request, pk):
        o = self._get(request, pk)
        if o.children.exists():
            raise serializers.ValidationError({"detail": "این ناحیه مدرسه دارد و قابل حذف نیست."})
        audit(request, "geo.region.delete", f"region:{o.pk}")
        o.delete()
        return Response(status=204)
