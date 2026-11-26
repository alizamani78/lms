from datetime import date, timedelta

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, ParentLink, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import ancestor_paths, org_in_scope

from .models import CalendarEvent

IsAdmin = role_required(*User.ADMIN_ROLES)


def visible_events(user, start, end):
    qs = CalendarEvent.objects.select_related("org").filter(start_date__lte=end, end_date__gte=start)
    if user.role == User.Role.SUPER_ADMIN:
        return qs
    from apps.core.scope import scope_paths
    managed = scope_paths(user) or []
    paths = set()
    for p in managed:
        paths |= set(ancestor_paths(p))
    if user.role == User.Role.PARENT:
        for o in OrgUnit.objects.filter(pk__in=ParentLink.objects.filter(parent=user, is_verified=True)
                                        .values_list("student__org_id", flat=True)):
            paths |= set(ancestor_paths(o.path))
    q = Q(org__isnull=True) | Q(org__path__in=paths)
    if user.role in User.ADMIN_ROLES:
        for p in managed:
            q |= Q(org__path__startswith=p)               # events created below, which the admin manages
    return qs.filter(q)


def _can_manage(user, event):
    if user.role == User.Role.SUPER_ADMIN:
        return True
    return user.role in User.ADMIN_ROLES and event.org_id is not None and org_in_scope(user, event.org)


class EventIn(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    kind = serializers.ChoiceField(choices=[c[0] for c in CalendarEvent.Kind.choices])
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    blocks_attendance = serializers.BooleanField(required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=300)
    nationwide = serializers.BooleanField(default=False)
    org = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, d):
        if d["end_date"] < d["start_date"]:
            raise serializers.ValidationError({"end_date": "تاریخ پایان نباید قبل از شروع باشد."})
        if (d["end_date"] - d["start_date"]).days > 120:
            raise serializers.ValidationError({"end_date": "حداکثر طول یک رویداد ۱۲۰ روز است."})
        return d


class CalendarView(APIView):
    def get(self, request):
        p = request.query_params
        try:
            start = date.fromisoformat(p["from"]) if p.get("from") else timezone.localdate() - timedelta(days=31)
            end = date.fromisoformat(p["to"]) if p.get("to") else timezone.localdate() + timedelta(days=92)
        except ValueError:
            return Response({"detail": "تاریخ نامعتبر است."}, status=400)
        if (end - start).days > 400:
            return Response({"detail": "بازه حداکثر ۴۰۰ روز."}, status=400)
        rows = visible_events(request.user, start, end).order_by("start_date")[:500]
        return Response([{
            "id": e.pk, "title": e.title, "kind": e.kind, "kind_label": e.get_kind_display(),
            "start_date": e.start_date, "end_date": e.end_date, "blocks_attendance": e.blocks_attendance,
            "description": e.description, "scope": e.org.name if e.org_id else "سراسر کشور", "org": e.org_id,
            "can_delete": _can_manage(request.user, e)} for e in rows])

    def post(self, request):
        if request.user.role not in User.ADMIN_ROLES:
            raise PermissionDenied()
        s = EventIn(data=request.data)
        s.is_valid(raise_exception=True)
        d, u = dict(s.validated_data), request.user
        nationwide, org_id = d.pop("nationwide"), d.pop("org", None)
        if nationwide:
            if u.role != User.Role.SUPER_ADMIN:
                raise PermissionDenied("فقط مدیر کل می‌تواند رویداد سراسری ثبت کند.")
            org = None
        else:
            org = get_object_or_404(OrgUnit, pk=org_id) if org_id else u.org
            if org is None or not org_in_scope(u, org):
                raise PermissionDenied()
        if "blocks_attendance" not in d:
            d["blocks_attendance"] = d["kind"] in ("holiday", "closure")
        e = CalendarEvent.objects.create(org=org, created_by=u, **d)
        audit(request, "calendar.create", f"event:{e.pk}")
        return Response({"id": e.pk}, status=201)


class CalendarDeleteView(APIView):
    def patch(self, request, pk):
        e = get_object_or_404(visible_events(request.user, date(1900, 1, 1), date(2999, 12, 31)), pk=pk)
        if not _can_manage(request.user, e):
            raise PermissionDenied()
        s = EventIn(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        for k in ("title", "kind", "start_date", "end_date", "blocks_attendance", "description"):
            if k in d:
                setattr(e, k, d[k])
        if e.end_date < e.start_date:
            raise serializers.ValidationError({"end_date": "تاریخ پایان نباید قبل از شروع باشد."})
        e.save()
        audit(request, "calendar.update", f"event:{e.pk}")
        return Response({"id": e.pk})

    def delete(self, request, pk):
        e = get_object_or_404(visible_events(request.user, date(1900, 1, 1), date(2999, 12, 31)), pk=pk)
        if not _can_manage(request.user, e):
            raise PermissionDenied()
        audit(request, "calendar.delete", f"event:{e.pk}")
        e.delete()
        return Response(status=204)


class OccasionListView(APIView):
    """GET /api/occasions/?from=YYYY-MM-DD&to=YYYY-MM-DD — official holidays and occasions of the range (any signed-in user)."""

    def get(self, request):
        from .models import Occasion
        p = request.query_params
        try:
            start = date.fromisoformat(p["from"]) if p.get("from") else timezone.localdate() - timedelta(days=31)
            end = date.fromisoformat(p["to"]) if p.get("to") else timezone.localdate() + timedelta(days=92)
        except ValueError:
            return Response({"detail": "تاریخ نامعتبر است."}, status=400)
        if (end - start).days > 400:
            return Response({"detail": "بازه حداکثر ۴۰۰ روز."}, status=400)
        rows = Occasion.objects.filter(is_active=True, gregorian_date__gte=start, gregorian_date__lte=end)[:800]
        return Response([{"id": o.pk, "title": o.title, "category": o.category, "is_holiday": o.is_holiday,
                          "date": o.gregorian_date, "jalali": f"{o.jalali_year}/{o.jalali_month:02d}/{o.jalali_day:02d}",
                          "description": o.description} for o in rows])
