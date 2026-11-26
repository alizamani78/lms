from rest_framework.permissions import BasePermission


def role_required(*roles):
    class _RoleRequired(BasePermission):
        def has_permission(self, request, view):
            u = request.user
            if not (u and u.is_authenticated):
                return False
            if u.role in roles:
                return True
            return u.extra_roles.filter(role__in=roles).exists()

    _RoleRequired.__name__ = "RoleRequired_" + "_".join(roles)
    return _RoleRequired
