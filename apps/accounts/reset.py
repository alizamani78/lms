import secrets

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope

from .models import User


class ResetPasswordView(APIView):
    """Admin sets a temporary password for a user below their rank inside their scope. Shown once."""
    permission_classes = [role_required(*User.ADMIN_ROLES)]

    def post(self, request, pk):
        u, target = request.user, get_object_or_404(User, pk=pk)
        if target.pk == u.pk:
            raise PermissionDenied("رمز خودتان را از بخش پروفایل تغییر دهید.")
        if u.role != User.Role.SUPER_ADMIN:
            if User.RANK[target.role] <= User.RANK[u.role] or target.is_superuser:
                raise PermissionDenied()
            if target.org is None or not org_in_scope(u, target.org):
                return Response(status=404)
        temp = secrets.token_urlsafe(12)
        target.set_password(temp)
        target.save(update_fields=["password"])
        audit(request, "user.reset_password", f"user:{target.pk}")
        return Response({"username": target.username, "temporary_password": temp,
                         "note": "این رمز فقط یک بار نمایش داده می‌شود؛ کاربر باید بلافاصله آن را تغییر دهد."})
