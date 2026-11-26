"""Organisation scoping. Every query on org-owned data goes through these helpers.

scope_paths(user):
  None  -> unrestricted (super admin)
  []    -> nothing
  [...] -> org path prefixes the user may act on
Principals and teachers work in every school they are a member of; other admins in their own subtree.
"""
from django.db.models import Q

from apps.accounts.models import User


def scope_paths(user):
    if user.role == User.Role.SUPER_ADMIN or user.is_superuser:
        return None
    # a manager who works in several schools sees one school at a time: the kartabl chosen for this request
    active = getattr(user, "_active_school_path", None)
    if active:
        return [active]
    if user.role in (User.Role.SCHOOL_ADMIN, User.Role.SCHOOL_DEPUTY_EDU, User.Role.SCHOOL_DEPUTY_EXEC, User.Role.SCHOOL_DEPUTY_CULTURE, User.Role.TEACHER):
        from apps.accounts.models import SchoolMembership
        paths = list(SchoolMembership.objects.filter(user=user).values_list("school__path", flat=True))
        # a teacher (or staff) who teaches classes in another school sees that school in the kartabl too
        from apps.courses.models import Course
        paths += list(Course.objects.filter(teacher=user).values_list("org__path", flat=True).distinct())
        return list(dict.fromkeys(p for p in paths if p))
    if user.role not in User.ADMIN_ROLES and user.role != User.Role.EXPERT:
        from apps.accounts.models import RoleAssignment
        extra = RoleAssignment.objects.filter(user=user, role__in=RoleAssignment.SCHOOL_ROLE_NAMES, school__isnull=False).values_list("school__path", flat=True)
        if extra.exists():
            from apps.accounts.models import SchoolMembership
            return list(SchoolMembership.objects.filter(user=user).values_list("school__path", flat=True)) + list(extra)
    if user.role in User.ADMIN_ROLES and user.org_id:
        return [user.org.path]
    if user.role == User.Role.EXPERT and user.org_id:
        return [user.org.path]
    return []


def scope_q(user, path_lookup):
    """Q restricting objects whose org path (given as ORM lookup, e.g. 'org__path') is inside the user's scope."""
    paths = scope_paths(user)
    if paths is None:
        return Q()
    if not paths:
        return Q(pk__in=[])
    q = Q()
    for p in paths:
        q |= Q(**{f"{path_lookup}__startswith": p})
    if user.role == User.Role.EXPERT and user.expert_kind == "curriculum" and user.expert_level_id:
        base = path_lookup[:-len("path")] if path_lookup.endswith("path") else path_lookup + "__"
        q &= Q(**{f"{base}profile__level_id": user.expert_level_id})      # only schools of the expert's level
    return q


def org_scope_q(user, field="org"):
    return scope_q(user, f"{field}__path")


def org_in_scope(user, org):
    paths = scope_paths(user)
    if paths is None:
        return True
    if org is None or not any(org.path.startswith(p) for p in paths):
        return False
    if user.role == User.Role.EXPERT and user.expert_kind == "curriculum" and user.expert_level_id:
        # a curriculum expert only reaches schools of its own level
        from apps.accounts.models import SchoolProfile
        if org.kind == "school":
            return SchoolProfile.objects.filter(school=org, level_id=user.expert_level_id).exists()
    return True


def ancestor_paths(path):
    """'/1/5/22/' -> ['/1/', '/1/5/', '/1/5/22/'] (the unit itself and every unit above it)."""
    ids = [i for i in path.strip("/").split("/") if i]
    return ["/" + "/".join(ids[:n]) + "/" for n in range(1, len(ids) + 1)]


def active_school_id(request):
    """The school selected in the kartabl (header X-School-Id or ?school=). Ignored unless the user may act on it."""
    from apps.accounts.models import OrgUnit
    raw = request.headers.get("X-School-Id") or request.query_params.get("school")
    if not raw or not str(raw).isdigit():
        return None
    school = OrgUnit.objects.filter(pk=int(raw), kind="school").first()
    if school is None or not org_in_scope(request.user, school):
        return None
    return school.pk


def kartabl_filter(request, field):
    """Keyword filter for list views: {field: active school} or {} when no school is selected."""
    sid = active_school_id(request)
    return {field: sid} if sid else {}


def user_level_ids(user):
    """Academic levels a user may see in grade, field and subject pickers. None = all levels.
    School-level staff see only the level(s) of their own school(s); curriculum experts their level; higher admins all."""
    from apps.accounts.models import SchoolMembership, SchoolProfile
    if user.role == User.Role.SUPER_ADMIN or user.is_superuser:
        return None
    if user.role in (User.Role.PROVINCE_ADMIN, User.Role.COUNTY_ADMIN, User.Role.REGION_ADMIN):
        return None
    if user.role == User.Role.EXPERT:
        if user.expert_kind == "curriculum" and user.expert_level_id:
            return {user.expert_level_id}
        return None
    school_ids = set(SchoolMembership.objects.filter(user=user).values_list("school_id", flat=True))
    if user.org_id:
        school_ids.add(user.org_id)
    levels = set(SchoolProfile.objects.filter(school_id__in=school_ids).values_list("level_id", flat=True))
    return levels
