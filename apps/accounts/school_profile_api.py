from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import Grade, Level, StudyField
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope

from .models import OrgUnit, SchoolProfile, User

STAFF = role_required(*User.STAFF_ROLES)


def _chain(school):
    """Province and region come from the school's position in the tree; the county is its own field."""
    out = {"province": "", "county": "", "region": "", "province_id": None, "county_id": None}
    node = school.parent
    while node is not None:
        if node.kind == "region" and not out["region"]:
            out["region"] = node.name
        if node.kind == "province":
            out["province"], out["province_id"] = node.name, node.pk
        node = node.parent
    prof = SchoolProfile.objects.select_related("county").filter(school=school).first()
    if prof is not None and prof.county_id:
        out["county"], out["county_id"] = prof.county.name, prof.county_id
    return out


def _dict(school, profile):
    base = {"school": {"id": school.pk, "name": school.name, "code": school.code or ""}, "address_chain": _chain(school),
            "region_id": school.parent_id if school.parent_id and school.parent.kind == "region" else None, "profile": None}
    if profile is not None:
        base["profile"] = {
            "school_code": profile.school_code or (school.code or ""), "level_id": profile.level_id, "level": profile.level.name,
            "school_type": profile.school_type, "shift": profile.shift, "ownership": profile.ownership,
            "gender": profile.gender, "country": profile.country, "postal_code": profile.postal_code,
            "address": profile.address, "phone": profile.phone, "fax": profile.fax, "county_id": profile.county_id,
            "website": profile.website, "email": profile.email}
    return base


class ProfileIn(serializers.Serializer):
    school_code = serializers.CharField(max_length=20)
    level = serializers.IntegerField()
    school_type = serializers.ChoiceField(choices=SchoolProfile.SchoolType.choices, default="normal")
    shift = serializers.ChoiceField(choices=SchoolProfile.Shift.choices, default="daily")
    ownership = serializers.ChoiceField(choices=SchoolProfile.Ownership.choices, default="public")
    gender = serializers.ChoiceField(choices=SchoolProfile.Gender.choices, default="mixed")
    country = serializers.CharField(max_length=60, default="ایران")
    postal_code = serializers.CharField(max_length=10, required=False, allow_blank=True)
    address = serializers.CharField(max_length=300, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    fax = serializers.CharField(max_length=20, required=False, allow_blank=True)
    website = serializers.URLField(max_length=200, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    region = serializers.IntegerField(required=False, allow_null=True)
    county = serializers.IntegerField(required=False, allow_null=True)


READ = role_required(*User.READ_ROLES)


class SchoolProfileView(APIView):
    """GET: anyone with a membership in the school. PUT: school admins / above inside their scope.
    'region' changes the school's parent unit (ناحیه/منطقه) — must be a region of the same county/province."""
    permission_classes = [READ]

    def _school(self, request, pk):
        school = get_object_or_404(OrgUnit, pk=pk, kind=OrgUnit.Kind.SCHOOL)
        if not org_in_scope(request.user, school):
            from django.http import Http404
            raise Http404()
        return school

    def get(self, request, pk):
        school = self._school(request, pk)
        profile = SchoolProfile.objects.select_related("level").filter(school=school).first()
        return Response(_dict(school, profile))

    def put(self, request, pk):
        if request.user.role not in User.ADMIN_ROLES:
            raise PermissionDenied()
        school = self._school(request, pk)
        s = ProfileIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        level = get_object_or_404(Level, pk=d.pop("level"))
        region_id = d.pop("region", None)
        county_id = d.pop("county", None)
        if county_id:
            d["county"] = get_object_or_404(OrgUnit, pk=county_id, kind=OrgUnit.Kind.COUNTY)
            top = school.parent
            while top is not None and top.kind != "province":
                top = top.parent
            if top is not None and not d["county"].path.startswith(top.path):
                raise serializers.ValidationError({"county": "شهرستان باید از همان استانِ مدرسه باشد."})
        else:
            d["county"] = None
        if region_id:
            region = get_object_or_404(OrgUnit, pk=region_id, kind=OrgUnit.Kind.REGION)
            if not org_in_scope(request.user, region):
                raise PermissionDenied("ناحیه خارج از محدوده شماست.")
            school.parent = region
            school.save()
        if SchoolProfile.objects.filter(school_code=d["school_code"]).exclude(school=school).exists():
            raise serializers.ValidationError({"school_code": "این کد آموزشگاه قبلاً برای مدرسه دیگری ثبت شده است."})
        profile, _ = SchoolProfile.objects.update_or_create(school=school, defaults={**d, "level": level})
        audit(request, "school.profile", f"school:{school.pk}")
        return Response(_dict(school, SchoolProfile.objects.select_related("level").get(pk=profile.pk)))


class CascadeView(APIView):
    def _levels(self, request):
        from apps.core.scope import user_level_ids
        return user_level_ids(request.user)

    def _grades(self, request):
        levels = self._levels(request)
        qs = Grade.objects.select_related("level").order_by("level__order", "order", "id")
        return qs if levels is None else qs.filter(level_id__in=levels)

    def _fields(self, request):
        levels = self._levels(request)
        qs = StudyField.objects.order_by("name")
        return qs if levels is None else qs.filter(level_id__in=levels)

    """GET /api/school-form/ -> the lists the school form and the report filters need (levels, types, shifts, ownerships)."""
    permission_classes = [READ]

    def get(self, request):
        extra_fields, extra_grades = [], []
        sid = request.query_params.get("school")
        if sid:
            from apps.academics.models import ClassRoom, Grade, StudyField
            classes = ClassRoom.objects.filter(school_id=sid, academic_year__is_current=True).select_related("grade", "study_field")
            fids = {c.study_field_id for c in classes if c.study_field_id}
            gids = {c.grade_id for c in classes if c.grade_id}
            extra_fields = [{"id": f.pk, "name": f.name} for f in StudyField.objects.filter(pk__in=fids).order_by("name")]
            extra_grades = [{"id": g.pk, "name": g.name} for g in Grade.objects.filter(pk__in=gids).order_by("name")]
        base = self._base_form(request)
        if extra_fields:
            seen = {f["id"] for f in base.get("fields", [])}
            base["fields"] = base.get("fields", []) + [f for f in extra_fields if f["id"] not in seen]
        if extra_grades:
            seen = {g["id"] for g in base.get("grades", [])}
            base["grades"] = base.get("grades", []) + [g for g in extra_grades if g["id"] not in seen]
        return Response(base)

    def _base_form(self, request):
        return {
            "levels": [{"id": l.pk, "name": l.name, "code": l.code or ""} for l in Level.objects.all()],
            "school_types": [{"value": v, "label": l} for v, l in SchoolProfile.SchoolType.choices],
            "shifts": [{"value": v, "label": l} for v, l in SchoolProfile.Shift.choices],
            "ownerships": [{"value": v, "label": l} for v, l in SchoolProfile.Ownership.choices],
            "genders": [{"value": v, "label": l} for v, l in SchoolProfile.Gender.choices],
            "grades": [{"id": g.pk, "name": g.name, "level": g.level.name} for g in self._grades(request)],
            "fields": [{"id": f.pk, "name": f.name} for f in self._fields(request)],
        }
