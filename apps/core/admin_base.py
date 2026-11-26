"""Role- and organisation-scoped Django admin.

Rules (enforced server-side on every request, not only by hiding menus):
  * super admins / superusers: everything
  * province/county/region/school admins: only objects inside their own org subtree, and only for the
    models/roles each ModelAdmin allows
  * teachers: only objects of their own courses, only where a ModelAdmin opts in
  * reference data (levels, grades, ...) is readable by admins, editable only by super admins
"""
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.accounts.models import User
from apps.core.scope import org_in_scope, scope_q

TEACHER = User.Role.TEACHER
# model name -> lookup (relative to that model) that leads to an org path
FK_SCOPE = {"OrgUnit": "path", "Course": "org__path", "ClassRoom": "school__path",
            "Exam": "course__org__path", "User": "org__path"}


def resolve(obj, lookup):
    if lookup == "self":
        return obj
    for part in lookup.split("__"):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


class ScopedAdminMixin:
    scope_lookup = None            # ORM path to the OrgUnit that owns the object ("self" for OrgUnit itself)
    teacher_lookup = None          # ORM path to the owning teacher; enables teacher access when set
    global_data = False            # reference data: readable by every allowed role
    read_roles = User.ADMIN_ROLES
    write_roles = ()               # () => only super admins may write

    # ---- helpers
    def _priv(self, request):
        u = request.user
        return bool(u.is_active and (u.is_superuser or u.role == User.Role.SUPER_ADMIN))

    def _role_ok(self, request, roles):
        u = request.user
        return bool(u.is_active and u.is_staff and (self._priv(request) or u.role in roles))

    def _scope_q(self, user):
        return scope_q(user, "path" if self.scope_lookup == "self" else f"{self.scope_lookup}__path")

    def _in_scope_org(self, request, org):
        return org_in_scope(request.user, org)

    def _orgs_to_check(self, obj):
        return [resolve(obj, self.scope_lookup)]

    # ---- permissions
    def has_module_permission(self, request):
        return self._role_ok(request, self.read_roles)

    def _visible(self, request, obj):
        return obj is None or self.get_queryset(request).filter(pk=obj.pk).exists()

    def has_view_permission(self, request, obj=None):
        return self._role_ok(request, self.read_roles) and self._visible(request, obj)

    def has_add_permission(self, request):
        return self._role_ok(request, self.write_roles)

    def has_change_permission(self, request, obj=None):
        return self._role_ok(request, self.write_roles) and self._visible(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._role_ok(request, self.write_roles) and self._visible(request, obj)

    # ---- data access
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        u = request.user
        if self._priv(request) or self.global_data:
            return qs
        if u.role == TEACHER:
            return qs.filter(**{self.teacher_lookup: u}) if self.teacher_lookup else qs.none()
        if not self.scope_lookup:
            return qs.none()
        return qs.filter(self._scope_q(u))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        u = request.user
        model = db_field.related_model
        if "queryset" not in kwargs and not self._priv(request):
            if u.role == TEACHER and model.__name__ == "Course":
                kwargs["queryset"] = model._default_manager.filter(teacher=u)
            elif model.__name__ in FK_SCOPE:
                kwargs["queryset"] = model._default_manager.filter(scope_q(u, FK_SCOPE[model.__name__]))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def _check(self, request, obj):
        """Reject writes that point outside the editor's own scope (defence in depth for raw ids etc.)."""
        u = request.user
        if self._priv(request) or self.global_data:
            return
        if u.role == TEACHER:
            if not self.teacher_lookup or resolve(obj, self.teacher_lookup) != u:
                raise PermissionDenied
            return
        orgs = self._orgs_to_check(obj)
        if not orgs or any(not self._in_scope_org(request, o) for o in orgs):
            raise PermissionDenied

    def save_model(self, request, obj, form, change):
        self._check(request, obj)
        super().save_model(request, obj, form, change)

    def delete_model(self, request, obj):
        self._check(request, obj)
        super().delete_model(request, obj)
