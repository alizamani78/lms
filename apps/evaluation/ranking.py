"""Ranks of students: inside the class, the school, the region and the province, overall and for each course.
Competition ranking: equal grades share a rank (1, 2, 2, 4). Only students with graded marks are ranked.
Ranks are computed from the same course grades as the evaluation (see logic.course_grade)."""
from collections import defaultdict

from django.core.cache import cache

from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit, User
from apps.core.scope import ancestor_paths

from .logic import course_grade, course_percentages, courses_with_final, policy_for, weights_for


def _ancestor(org, kind):
    node = org
    while node is not None:
        if node.kind == kind:
            return node
        node = node.parent
    return None


def competition_ranks(values):
    """values: {key: number}. Returns {key: rank}; higher is better; equal values share the rank."""
    items = sorted(((k, v) for k, v in values.items() if v is not None), key=lambda kv: -kv[1])
    out, last, rank = {}, None, 0
    for i, (k, v) in enumerate(items, start=1):
        if v != last:
            rank, last = i, v
        out[k] = rank
    return out


def _school_grades(school):
    """{student_id: {"overall": float|None, "courses": {course_id: grade}, "titles": {course_id: title}}}"""
    pct = course_percentages([school.pk])
    finals = courses_with_final([school.pk])
    wc, wf = weights_for(school)
    res = defaultdict(lambda: {"courses": {}, "titles": {}})
    for (sid, cid), (title, cls, cont, fin) in pct.items():
        grade, _, _ = course_grade(cont, fin, cid in finals, wc, wf)
        if grade is None:
            continue
        res[sid]["courses"][cid] = grade
        res[sid]["titles"][cid] = title
    for sid, d in res.items():
        vals = list(d["courses"].values())
        d["overall"] = sum(vals) / len(vals) if vals else None
    return dict(res)


def province_table(org):
    """Grades of every student in every school under `org` (cached 10 minutes)."""
    key = f"rank-table:{org.pk}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    table = {}
    schools = OrgUnit.objects.filter(kind="school", path__startswith=org.path)
    for school in schools:
        for sid, d in _school_grades(school).items():
            table[sid] = {"school": school.pk, **d}
    cache.set(key, table, 600)
    return table


def class_of_students(school):
    out = {}
    for c in ClassRoom.objects.filter(school=school, academic_year__is_current=True).prefetch_related("students"):
        for s in c.students.all():
            out[s.pk] = c.pk
    return out


def _rank_maps(table, keys_scope):
    """Overall ranks for students whose school matches keys_scope(school_id)."""
    overall = {sid: d["overall"] for sid, d in table.items() if keys_scope(d["school"])}
    return competition_ranks(overall)


def ranks_for_school_students(school, region, province):
    """{student_id: {"class_rank","school_rank","region_rank","province_rank","overall","class_id"}} for one school."""
    own = _school_grades(school)
    classes = class_of_students(school)
    region_table = province_table(region) if region is not None else {}
    province_t = province_table(province) if province is not None else {}
    school_ov = competition_ranks({sid: d["overall"] for sid, d in own.items()})
    class_ov = {}
    for cid in set(classes.values()):
        members = {sid: own[sid]["overall"] for sid in own if classes.get(sid) == cid}
        class_ov[cid] = competition_ranks(members)
    region_ov = competition_ranks({sid: d["overall"] for sid, d in region_table.items()}) if region_table else {}
    province_ov = competition_ranks({sid: d["overall"] for sid, d in province_t.items()}) if province_t else {}
    out = {}
    for sid, d in own.items():
        if d["overall"] is None:
            continue
        cid = classes.get(sid)
        out[sid] = {"overall": round(d["overall"], 1), "class_id": cid,
                    "class_rank": class_ov.get(cid, {}).get(sid), "school_rank": school_ov.get(sid),
                    "region_rank": region_ov.get(sid), "province_rank": province_ov.get(sid)}
    return out, own


def course_ranks(school, region, province, course_id):
    """For one course: rank in class, school, region and province (grades of that course only)."""
    own_pct = {sid: d["courses"][course_id] for sid, d in _school_grades(school).items() if course_id in d["courses"]}
    classes = class_of_students(school)
    school_r = competition_ranks(own_pct)
    class_r = {}
    for cid in set(classes.get(s) for s in own_pct):
        class_r.update(competition_ranks({s: g for s, g in own_pct.items() if classes.get(s) == cid}))
    region_r, province_r = {}, {}
    if region is not None:
        t = province_table(region)
        region_r = competition_ranks({s: d["courses"].get(course_id) for s, d in t.items() if course_id in d["courses"]})
    if province is not None:
        t = province_table(province)
        province_r = competition_ranks({s: d["courses"].get(course_id) for s, d in t.items() if course_id in d["courses"]})
    return own_pct, {"class": class_r, "school": school_r, "region": region_r, "province": province_r}
