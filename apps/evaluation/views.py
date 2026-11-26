from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope

from .logic import course_percentages, courses_with_final, evaluate_students, policy_for, weights_for
from .models import DEFAULT_BANDS, EvaluationPolicy

IsAdmin = role_required(*User.ADMIN_ROLES)


def _policy_payload(org):
    p, pass_mark, bands = policy_for(org)
    own = EvaluationPolicy.objects.filter(org=org).first() if org is not None else EvaluationPolicy.objects.filter(org=None).first()
    wc, wf = weights_for(org)
    return {"org": org.pk if org else None, "org_name": org.name if org else "کشور",
            "pass_percent": pass_mark, "bands": bands, "defined_here": own is not None,
            "continuous_weight": wc, "final_weight": wf,
            "source": (p.org.name if p and p.org_id else "پیش‌فرض کشوری") if p else "پیش‌فرض کشوری"}


class EvaluationPolicyView(APIView):
    """GET ?org=<id>  -> the policy that applies there.  PUT {org, pass_percent, bands:[{label,min}]} -> save at that level.
    Each level sets its own policy; admins may change only the level inside their scope (super admin: any, incl. national)."""
    permission_classes = [IsAdmin]

    def get(self, request):
        org = None
        if request.query_params.get("org", "").isdigit():
            org = get_object_or_404(OrgUnit, pk=int(request.query_params["org"]))
            if not org_in_scope(request.user, org):
                raise PermissionDenied()
        return Response(_policy_payload(org))

    def put(self, request):
        d = request.data or {}
        org = None
        if str(d.get("org") or "").isdigit():
            org = get_object_or_404(OrgUnit, pk=int(d["org"]))
        if org is None and request.user.role != User.Role.SUPER_ADMIN:
            raise PermissionDenied("سیاست کشوری فقط توسط مدیر کل تغییر می‌کند.")
        if org is not None and not org_in_scope(request.user, org):
            raise PermissionDenied()
        bands = d.get("bands") or DEFAULT_BANDS
        if not isinstance(bands, list) or not (1 <= len(bands) <= 10):
            raise serializers.ValidationError({"bands": "بازه‌ها نامعتبر است."})
        clean = []
        for b in bands:
            try:
                clean.append({"label": str(b["label"]).strip()[:30], "min": float(b["min"])})
            except (KeyError, TypeError, ValueError):
                raise serializers.ValidationError({"bands": "هر بازه باید برچسب و حد پایین داشته باشد."})
        try:
            pass_pct = Decimal(str(d.get("pass_percent", 50)))
        except Exception:
            raise serializers.ValidationError({"pass_percent": "عدد نامعتبر است."})
        try:
            wc, wf = Decimal(str(d.get("continuous_weight", 1))), Decimal(str(d.get("final_weight", 1)))
        except Exception:
            raise serializers.ValidationError({"final_weight": "ضریب نامعتبر است."})
        p, _ = EvaluationPolicy.objects.update_or_create(org=org, defaults={"pass_percent": pass_pct, "bands": clean,
                                                                            "continuous_weight": wc, "final_weight": wf,
                                                                            "updated_by": request.user})
        try:
            p.full_clean()
        except Exception as e:
            raise serializers.ValidationError(getattr(e, "message_dict", {"detail": str(e)}))
        audit(request, "evaluation.policy", f"org:{org.pk if org else 'national'}")
        return Response(_policy_payload(org))


class MyEvaluationView(APIView):
    """A student's own marks: averages per course, overall average, pass/fail and rating band."""
    permission_classes = [role_required(User.Role.STUDENT)]

    def get(self, request):
        school = request.user.org
        if school is None or school.kind != "school":
            return Response({"courses": [], "average": None})
        pct = course_percentages([school.pk], student_ids=[request.user.pk])
        policy, pass_mark, bands = policy_for(school)
        rows = evaluate_students(school, pct, pass_mark, bands, finals=courses_with_final([school.pk]),
                                 weights=weights_for(school))
        row = rows[0] if rows else None
        return Response({"pass_percent": pass_mark, "average": row["average"] if row else None,
                         "status": row["status"] if row else None, "band": row["band"] if row else None,
                         "courses": row["course_detail"] if row else []})
