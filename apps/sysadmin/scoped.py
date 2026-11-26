"""Role-scoped management for school/region/county/province admins: the same forms as the system panel, limited to the
caller's own scope and rank. This is what replaces the Django admin for everyone who is not the system administrator.

Rules (enforced on the server, never only in the browser):
  * a list shows only objects inside the caller's organisation scope;
  * an object can only be created or moved inside that scope;
  * users: only roles BELOW the caller's rank (an expert only by region admins and above); never super admins;
    users cannot be deleted, only deactivated;
  * principals reach only the schools they are members of; regional admins reach every school below their unit.
"""
from copy import deepcopy

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicYear, ClassRoom, Grade, Level, StudyField, Subject
from apps.accounts.models import OrgUnit, User
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope, scope_q

from .resources import RESOURCES
from .views import (ResourceCreateView, ResourceDetailView, ResourceListView, ScopedMixin, schema_for, _label)

IsAdmin = role_required(*User.ADMIN_ROLES)

# resource -> (path lookup used for scoping, foreign keys that must stay inside the scope, minimum admin rank)
SCOPES = {
    "users": ("org__path", ["org"], 4),
    "memberships": ("school__path", ["school"], 4),
    "parent_links": ("student__org__path", [], 4),
    "classes": ("school__path", ["school"], 4),
    "courses": ("org__path", ["org"], 4),
    "enrollments": ("course__org__path", [], 4),
    "school_profiles": ("school__path", ["school"], 4),
    "schools": ("path", ["parent"], 3),
    "bell_periods": ("school__path", ["school"], 4),
    "calendar": ("org__path", ["org"], 4),
    "sms_gateways": ("school__path", ["school"], 4),
    "schedule": ("classroom__school__path", [], 4),
}
NO_DELETE = {"users"}


def allowed_keys(user):
    rank = User.RANK[user.role]
    return [k for k, (_, _, need) in SCOPES.items() if rank <= need]


def lower_roles(user):
    rank = User.RANK[user.role]
    roles = [r for r, v in User.RANK.items() if v > rank and r != "super_admin"]
    if rank > User.RANK["region_admin"]:
        roles = [r for r in roles if r != "expert"]          # only regional admins and above create experts
    return roles


def _resources_for(user):
    out = {}
    for key in allowed_keys(user):
        r = deepcopy({k: v for k, v in RESOURCES[key].items() if k != "qs"})
        r["model"] = RESOURCES[key]["model"]
        if "qs" in RESOURCES[key]:
            r["qs"] = RESOURCES[key]["qs"]
        if key == "users":
            r["choice_filter"] = {"role": lower_roles(user)}
        if key in NO_DELETE:
            r["deletable"] = False
        # SMS secrets are never editable here (the system administrator or the school API sets them)
        out[key] = r
    return out


class ManageSchemaView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        res = _resources_for(request.user)
        return Response(schema_for(list(res.keys()), res))


class _ManageBase(ScopedMixin):
    permission_classes = [IsAdmin]

    def resource(self, res):
        r = _resources_for(self.request.user).get(res)
        if r is None:
            from django.http import Http404
            raise Http404()
        r["_key"] = res
        return r

    def queryset(self, r):
        base = r["qs"]() if "qs" in r else r["model"].objects.all()
        if r.get("kind"):
            base = base.filter(kind=r["kind"])
        user = self.request.user
        base = base.filter(scope_q(user, SCOPES[r["_key"]][0]))
        if r["_key"] == "users":
            base = base.filter(role__in=lower_roles(user), is_superuser=False)
        return base

    def deletable(self, r):
        return r.get("deletable", True)

    def before_save(self, r, obj, is_new):
        """Keep every organisation reference inside the caller's scope, and users below the caller's rank."""
        user = self.request.user
        key = r["_key"]
        for fk in SCOPES[key][1]:
            org = getattr(obj, fk, None)
            if org is None or not org_in_scope(user, org):
                raise PermissionDenied("این واحد سازمانی خارج از محدوده شماست.")
        if key == "schedule" and not org_in_scope(user, obj.classroom.school):
            raise PermissionDenied("کلاس خارج از محدوده شماست.")
        if key == "enrollments" and not org_in_scope(user, obj.course.org):
            raise PermissionDenied("درس خارج از محدوده شماست.")
        if key == "parent_links" and (obj.student.org is None or not org_in_scope(user, obj.student.org)):
            raise PermissionDenied("دانش‌آموز خارج از محدوده شماست.")
        if key == "schools" and is_new and obj.parent.kind not in ("region", "county", "province"):
            raise serializers.ValidationError({"parent": "مدرسه باید زیر ناحیه یا شهرستان یا استان باشد."})
        if key == "users":
            if obj.role not in lower_roles(user) or obj.is_superuser:
                raise PermissionDenied("این نقش برای شما قابل تعریف نیست.")
            if obj.org is None or not org_in_scope(user, obj.org):
                raise PermissionDenied("مدرسه/واحد کاربر باید در محدوده شما باشد.")
            if not is_new and obj.pk == user.pk:
                raise PermissionDenied("حساب خودتان را از پروفایل ویرایش کنید.")
        if key == "memberships":
            if obj.user.role not in ("teacher", "school_admin", "school_deputy_edu", "school_deputy_exec", "school_deputy_culture") or User.RANK[obj.user.role] <= User.RANK[user.role]:
                raise PermissionDenied("این کاربر را نمی‌توانید به مدرسه وصل کنید.")


class ManageListView(_ManageBase, ResourceListView):
    pass


class ManageCreateView(_ManageBase, ResourceCreateView):
    pass


class ManageDetailView(_ManageBase, ResourceDetailView):
    pass


class ManageOptionsView(APIView):
    """Choices for pickers, restricted to the caller's scope (catalogue lists are global). Staff read them too."""
    permission_classes = [role_required(*User.STAFF_ROLES)]

    def get(self, request):
        from .options import build_options
        return Response(build_options(request, scoped=True))
