"""Additional roles of a person, edited inside the person's form (system administrator)."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import RoleAssignment, User
from apps.core.models import audit
from apps.core.permissions import role_required

SuperOnly = role_required(User.Role.SUPER_ADMIN)


def _row(x):
    return {"id": x.pk, "role": x.role, "role_label": x.get_role_display(), "school": x.school_id,
            "school_name": x.school.name if x.school_id else ""}


class UserRolesView(APIView):
    """GET ?user=ID — the additional roles of a person. POST {user, role, school} — add one (duplicates are refused)."""
    permission_classes = [SuperOnly]

    def get(self, request):
        uid = request.query_params.get("user") or ""
        if not uid.isdigit():
            raise serializers.ValidationError({"user": "کاربر را انتخاب کنید."})
        return Response([_row(x) for x in RoleAssignment.objects.filter(user_id=int(uid)).select_related("school")])

    def post(self, request):
        user = get_object_or_404(User, pk=request.data.get("user") or 0)
        x = RoleAssignment(user=user, role=str(request.data.get("role") or ""), school_id=request.data.get("school") or None)
        if RoleAssignment.objects.filter(user=user, role=x.role, school_id=x.school_id).exists():
            raise serializers.ValidationError({"role": "این نقش برای این کاربر و مدرسه از قبل ثبت است (تکراری)."})
        x.full_clean()
        x.save()
        audit(request, "user.role.add", f"user:{user.pk} role:{x.role}")
        return Response(_row(x), status=201)


class UserRoleDetailView(APIView):
    permission_classes = [SuperOnly]

    def delete(self, request, pk):
        x = get_object_or_404(RoleAssignment, pk=pk)
        audit(request, "user.role.remove", f"user:{x.user_id} role:{x.role}")
        x.delete()
        return Response(status=204)
