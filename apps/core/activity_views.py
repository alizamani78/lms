from datetime import datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.activity import LABELS, activity_q
from apps.core.models import AuditLog
from apps.core.permissions import role_required

IsAdmin = role_required(*User.ADMIN_ROLES)


class ActivityView(APIView):
    """GET /api/activity/?q=<name|username|national id>&action=<code>&unit=<org id>&from=YYYY-MM-DD&to=YYYY-MM-DD
    Who logged in, who opened a task or an exam, and what was done after that. Visible to the system administrator
    (everyone) and to other administrators (their own scope)."""
    permission_classes = [IsAdmin]

    def get(self, request):
        p = request.query_params
        qs = AuditLog.objects.select_related("user", "user__org").filter(activity_q(request.user))
        qs = qs.filter(action__in=list(LABELS.keys()))
        if p.get("action") in LABELS:
            qs = qs.filter(action=p["action"])
        q = p.get("q", "").strip()[:60]
        if q:
            qs = qs.filter(Q(user__username__icontains=q) | Q(user__national_id__startswith=q)
                           | Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q))
        if p.get("unit", "").isdigit():
            from apps.accounts.models import OrgUnit
            unit = OrgUnit.objects.filter(pk=int(p["unit"])).first()
            qs = qs.filter(user__org__path__startswith=unit.path) if unit else qs.none()
        tz = timezone.get_current_timezone()
        try:
            start = datetime.combine(datetime.fromisoformat(p["from"]).date(), time.min, tzinfo=tz) if p.get("from") else None
            end = datetime.combine(datetime.fromisoformat(p["to"]).date() + timedelta(days=1), time.min, tzinfo=tz) if p.get("to") else None
        except ValueError:
            return Response({"detail": "تاریخ نامعتبر است."}, status=400)
        if start:
            qs = qs.filter(created_at__gte=start)
        if end:
            qs = qs.filter(created_at__lt=end)
        rows = []
        for a in qs.order_by("-created_at")[:500]:
            u = a.user
            rows.append({"at": a.created_at, "name": (u.get_full_name() or u.username) if u else "—",
                         "username": u.username if u else "", "role": u.get_role_display() if u else "",
                         "school": (u.org.name if u and u.org_id else ""), "action": LABELS.get(a.action, a.action),
                         "code": a.action, "target": a.target, "ip": a.ip or ""})
        return Response({"rows": rows, "count": len(rows)})
