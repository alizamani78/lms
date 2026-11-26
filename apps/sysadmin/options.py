"""Choices for foreign-key pickers: filtered by kind / role / parent and searchable (never a list of everything).
Used by the system panel (everything) and by scoped admins (only their own scope)."""
from django.db.models import Q
from rest_framework import serializers

from apps.academics.models import AcademicYear, ClassRoom, Grade, Level, StudyField, Subject
from apps.accounts.models import OrgUnit, User
from apps.core.scope import scope_q

LIMIT = 100


def _ids(value):
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def org_label(o):
    if o.kind == "school":
        parent = o.parent.name if o.parent_id else ""
        return f"{o.name} — {parent}" if parent else o.name
    return o.name


def user_label(u):
    full = u.get_full_name()
    return f"{full} ({u.username})" if full else u.username


def school_level_of(school_id):
    """Level id of a school (from its profile), or None when the school or its profile is not known."""
    if not str(school_id or "").isdigit():
        return None
    from apps.accounts.models import SchoolProfile
    row = SchoolProfile.objects.filter(school_id=int(school_id)).values_list("level_id", flat=True).first()
    return row


def build_options(request, scoped):
    p, user = request.query_params, request.user
    model, q = p.get("model", ""), p.get("q", "").strip()[:60]
    if model == "accounts.OrgUnit":
        qs = OrgUnit.objects.select_related("parent")
        if scoped:
            qs = qs.filter(scope_q(user, "path"))
        kinds = _ids(p.get("kind"))
        if kinds:
            qs = qs.filter(kind__in=kinds)
        if p.get("parent", "").isdigit():
            qs = qs.filter(parent_id=int(p["parent"]))
        for key in ("province", "region", "county"):
            if p.get(key, "").isdigit():
                unit = OrgUnit.objects.filter(pk=int(p[key])).first()
                qs = qs.filter(path__startswith=unit.path) if unit else qs.none()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q))
        return [{"id": o.pk, "label": org_label(o)} for o in qs.order_by("name")[:LIMIT]]
    if model == "accounts.User":
        qs = User.objects.select_related("org").filter(is_superuser=False) if scoped else User.objects.select_related("org")
        if scoped:
            qs = qs.filter(scope_q(user, "org__path"))
        roles = _ids(p.get("role"))
        if roles:
            qs = qs.filter(role__in=roles)
        if p.get("school", "").isdigit():
            unit = OrgUnit.objects.filter(pk=int(p["school"])).first()
            qs = qs.filter(org__path__startswith=unit.path) if unit else qs.none()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                           | Q(national_id__startswith=q) | Q(student_code__startswith=q))
        return [{"id": u.pk, "label": user_label(u)} for u in qs.order_by("last_name", "username")[:LIMIT]]
    if model == "academics.ClassRoom":
        qs = ClassRoom.objects.select_related("school", "academic_year")
        if scoped:
            qs = qs.filter(scope_q(user, "school__path"))
        if p.get("school", "").isdigit():
            qs = qs.filter(school_id=int(p["school"]))
        if q:
            qs = qs.filter(name__icontains=q)
        return [{"id": c.pk, "label": f"{c.name} — {c.school.name} ({c.academic_year.name})"} for c in qs.order_by("name")[:LIMIT]]
    if model == "courses.Course":
        from apps.courses.models import Course
        qs = Course.objects.select_related("org", "classroom")
        if scoped:
            qs = qs.filter(scope_q(user, "org__path"))
        if p.get("school", "").isdigit():
            qs = qs.filter(org_id=int(p["school"]))
        if p.get("classroom", "").isdigit():
            qs = qs.filter(classroom_id=int(p["classroom"]))
        if q:
            qs = qs.filter(title__icontains=q)
        return [{"id": c.pk, "label": f"{c.title}" + (f" — {c.classroom.name}" if c.classroom_id else "") + f" — {c.org.name}"}
                for c in qs.order_by("title")[:LIMIT]]
    if model == "timetable.BellPeriod":
        from apps.timetable.models import BellPeriod
        qs = BellPeriod.objects.select_related("school")
        if scoped:
            qs = qs.filter(scope_q(user, "school__path"))
        if p.get("school", "").isdigit():
            qs = qs.filter(school_id=int(p["school"]))
        return [{"id": b.pk, "label": f"زنگ {b.number} ({b.start_time:%H:%M}–{b.end_time:%H:%M}) — {b.school.name}"} for b in qs.order_by("number")[:LIMIT]]
    simple = {"academics.Level": Level, "academics.Grade": Grade, "academics.StudyField": StudyField,
              "academics.AcademicYear": AcademicYear, "academics.Subject": Subject}
    if model == "academics.Level" and p.get("combo") == "1":
        # the level and its branches in one list: "متوسطه دوم — کاردانش" is one choice
        from apps.imports.rules import _KNOWN_BRANCHES, _is_secondary
        out = []
        from apps.core.persian import normalize_text as _nt
        not_levels = {_nt(x) for x in ("کاردانش", "فنی و حرفه‌ای", "فنی حرفه‌ای و کاردانش", "حرفه‌ای استثنایی", "عمومی", "نظری")}
        for lv in Level.objects.order_by("order", "name"):
            if _nt(lv.name) in not_levels:
                continue                      # a branch, not a level: it is chosen with its level
            if _is_secondary(lv):
                for br in _KNOWN_BRANCHES:
                    if br != "عمومی":
                        out.append({"id": f"{lv.pk}:{br}", "label": f"{lv.name} — {br}"})
            else:
                out.append({"id": f"{lv.pk}:", "label": lv.name})
        return out
    if model == "academics.Grade" and p.get("school", "").isdigit():
        from apps.accounts.models import SchoolProfile as _SPg
        prof = _SPg.objects.filter(school_id=int(p["school"])).select_related("level").first()
        if prof is not None and prof.level_id:
            out = []
            for g in Grade.objects.filter(level_id=prof.level_id).select_related("level", "branch"):
                label = f"{g.name} — {g.level.name}" + (f" — {g.branch.name}" if g.branch_id else "")
                out.append({"id": g.pk, "label": label})
            return out
    if model == "academics.Subject" and p.get("grade", "").isdigit():
        # the lessons of a grade (its general lessons, and its field's lessons when a field is given)
        from django.db.models import Q as _QS
        qs = Subject.objects.filter(grades__pk=int(p["grade"]))
        fid = p.get("field", "")
        if fid.isdigit():
            qs = qs.filter(_QS(sharing="general") | _QS(study_fields__isnull=True) | _QS(study_fields__pk=int(fid)))
        else:
            qs = qs.filter(_QS(sharing="general") | _QS(study_fields__isnull=True))
        return [{"id": x.pk, "label": x.name} for x in qs.distinct().order_by("name")[:LIMIT]]
    if model in simple:
        qs = simple[model].objects.all()
        school_level = school_level_of(p.get("school"))
        if school_level is None and scoped:
            from apps.core.scope import user_level_ids
            allowed = user_level_ids(user)
            if allowed is not None:
                if model in ("academics.Grade", "academics.StudyField"):
                    qs = qs.filter(level_id__in=allowed)
                elif model == "academics.Subject":
                    qs = qs.filter(grades__level_id__in=allowed).distinct()
        if school_level is not None:
            # a school shows only its own level: its grades, its fields and the subjects offered in its grades
            if model == "academics.Grade":
                qs = qs.filter(level_id=school_level)
            elif model == "academics.StudyField":
                qs = qs.filter(level_id=school_level)
                if p.get("school", "").isdigit():
                    from django.db.models import Q as _Q
                    from apps.accounts.models import SchoolProfile as _SP
                    qs = qs.filter(schools__school_id=int(p["school"])).distinct()     # only the school's own fields
                    prof = _SP.objects.filter(school_id=int(p["school"])).first()
                    if prof is not None and prof.branch:                               # and of the school's branch
                        qs = qs.filter(_Q(branch__name=prof.branch) | _Q(branch__isnull=True)).distinct()
            elif model == "academics.Subject":
                from django.db.models import Q as _Q
                qs = qs.filter(grades__level_id=school_level).distinct()
                if p.get("school", "").isdigit():
                    qs = qs.filter(_Q(sharing="general") | _Q(sharing="", study_fields__isnull=True)
                                   | _Q(study_fields__schools__school_id=int(p["school"]))).distinct()
        if q and hasattr(simple[model], "name"):
            qs = qs.filter(name__icontains=q)
        return [{"id": o.pk, "label": str(o)} for o in qs.order_by("pk")[:300]]
    raise serializers.ValidationError({"model": "نامعتبر"})
