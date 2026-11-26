from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.permissions import role_required

from .models import Subject

STAFF = role_required(*User.STAFF_ROLES)


def filter_subjects(qs, params):
    """Filter the curriculum catalogue by grade id, study-field id and free text."""
    if params.get("grade", "").isdigit():
        qs = qs.filter(grades__id=int(params["grade"]))
    if params.get("field", "").isdigit():
        qs = qs.filter(study_fields__id=int(params["field"]))
    if params.get("q", "").strip():
        q = params["q"].strip()[:100]
        qs = qs.filter(Q(name__icontains=q) | Q(code__startswith=q))
    return qs


class SubjectListView(APIView):
    """GET /api/subjects/?grade=<id>&field=<id>&q=<text> — the curriculum catalogue (read-only for staff)."""
    permission_classes = [STAFF]

    def get(self, request):
        from apps.core.scope import user_level_ids
        allowed = user_level_ids(request.user)
        base = Subject.objects.prefetch_related("grades", "study_fields").distinct()
        if allowed is not None:
            base = base.filter(grades__level_id__in=allowed).distinct()
        qs = filter_subjects(base, request.query_params)
        return Response([{
            "id": s.pk, "code": s.code, "name": s.name, "subject_type": s.subject_type,
            "weekly_hours": s.weekly_hours,
            "grades": [{"id": g.pk, "name": g.name} for g in s.grades.all()],
            "fields": [{"id": f.pk, "name": f.name} for f in s.study_fields.all()],
        } for s in qs.order_by("name", "code")[:500]])
