"""Scoped school-system reports. Every report runs only on objects the caller may see (scope_q / org_in_scope).
District reports: region, county, province admins and assessment experts (any school in their unit).
School reports: any of those (for one chosen school) and the principal of that school.
Teachers and students have no report catalogue here."""
from collections import defaultdict

from django.db.models import Avg, Count, Q

from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit, SchoolMembership, SchoolProfile, User
from apps.core.scope import org_in_scope, scope_q
from apps.courses.models import Course
from apps.exams.models import Attempt
from apps.evaluation.ranking import competition_ranks

DISTRICT_ROLES = ("super_admin", "province_admin", "county_admin", "region_admin", "expert")
SCHOOL_ROLES = DISTRICT_ROLES + ("school_admin", "school_deputy_exec", "school_deputy_edu")


FILTER_KEYS = ("province", "county", "region", "level", "school_type", "shift", "ownership", "gender", "q")


def _filtered_schools(user, org, f=None):
    """Schools inside the user's scope, narrowed by the full filter set (province, county, region, level, type, shift,
    ownership, gender, name/code text). Used by every district report and by the directory."""
    f = f or {}
    qs = OrgUnit.objects.filter(kind="school").filter(scope_q(user, "path")).select_related("parent")
    if org is not None:
        qs = qs.filter(path__startswith=org.path)
    for key in ("province", "region"):
        if str(f.get(key) or "").isdigit():
            unit = OrgUnit.objects.filter(pk=int(f[key])).first()
            qs = qs.filter(path__startswith=unit.path) if unit else qs.none()
    if str(f.get("county") or "").isdigit():
        qs = qs.filter(profile__county_id=int(f["county"]))
    if str(f.get("level") or "").isdigit():
        qs = qs.filter(profile__level_id=int(f["level"]))
    for key, field in (("school_type", "school_type"), ("shift", "shift"), ("ownership", "ownership"), ("gender", "gender")):
        if f.get(key):
            qs = qs.filter(**{f"profile__{field}": f[key]})
    if f.get("q"):
        qs = qs.filter(Q(name__icontains=str(f["q"])[:60]) | Q(profile__school_code__icontains=str(f["q"])[:60]))
    return qs


def _school_ids(user, org, f=None):
    return list(_filtered_schools(user, org, f).values_list("pk", flat=True))


def _name(u):
    return u.get_full_name() or u.username


# ------------------------------------------------------------------ district (region / county / province)
def district_schools(user, org, f=None):
    ids = _school_ids(user, org, f)
    schools = OrgUnit.objects.filter(pk__in=ids).select_related("parent").order_by("name")
    profiles = {p.school_id: p for p in SchoolProfile.objects.select_related("level").filter(school_id__in=ids)}
    students = dict(User.objects.filter(role="student", org_id__in=ids).values_list("org_id").annotate(n=Count("id")))
    teachers = dict(SchoolMembership.objects.filter(school_id__in=ids, user__role="teacher")
                    .values_list("school_id").annotate(n=Count("user", distinct=True)))
    classes = dict(ClassRoom.objects.filter(school_id__in=ids, academic_year__is_current=True)
                   .values_list("school_id").annotate(n=Count("id")))
    fields = defaultdict(list)
    for row in (ClassRoom.objects.filter(school_id__in=ids, academic_year__is_current=True, study_field__isnull=False)
                .values("school_id", "study_field__name").annotate(n=Count("id")).order_by("study_field__name")):
        fields[row["school_id"]].append(f'{row["study_field__name"]} ({row["n"]})')
    rows = []
    for s in schools:
        p = profiles.get(s.pk)
        rows.append({"school": s.name, "school_code": p.school_code if p else "", "level": p.level.name if p else "",
                     "region": s.parent.name if s.parent_id else "", "classes": classes.get(s.pk, 0),
                     "students": students.get(s.pk, 0), "teachers": teachers.get(s.pk, 0),
                     "fields": "، ".join(fields.get(s.pk, []))})
    return rows


def report_district(user, org, classroom=None, **kw):
    rows = district_schools(user, org, kw.get("filters"))
    totals = {k: sum(r[k] for r in rows) for k in ("classes", "students", "teachers")}
    return {"columns": [("school", "مدرسه"), ("school_code", "کد آموزشگاه"), ("level", "مقطع"), ("region", "ناحیه"),
                        ("classes", "کلاس‌ها"), ("students", "دانش‌آموزان"), ("teachers", "معلمان"), ("fields", "رشته‌ها (تعداد کلاس)")],
            "rows": rows, "totals": {**totals, "schools": len(rows)}}


def report_fields(user, org, classroom=None, **kw):
    ids = _school_ids(user, org, kw.get("filters"))
    data = (ClassRoom.objects.filter(school_id__in=ids, academic_year__is_current=True, study_field__isnull=False)
            .values("study_field__name").annotate(classes=Count("id", distinct=True), students=Count("students", distinct=True))
            .order_by("study_field__name"))
    rows = [{"field": d["study_field__name"], "classes": d["classes"], "students": d["students"]} for d in data]
    return {"columns": [("field", "رشته"), ("classes", "کلاس‌ها"), ("students", "دانش‌آموزان")], "rows": rows,
            "totals": {"classes": sum(r["classes"] for r in rows), "students": sum(r["students"] for r in rows)}}


# ------------------------------------------------------------------ school
def _one_school(user, org):
    if org is None or org.kind != "school" or not org_in_scope(user, org):
        raise ValueError("مدرسه‌ای در محدوده شما انتخاب کنید.")
    return org


def report_classes(user, org, classroom=None, **kw):
    school = _one_school(user, org)
    qs = ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field", "homeroom_teacher")
    if classroom is not None:
        qs = qs.filter(pk=classroom.pk)
    counts = dict(ClassRoom.students.through.objects.filter(classroom__in=qs).values_list("classroom_id").annotate(n=Count("id")))
    courses = dict(Course.objects.filter(classroom__in=qs).values_list("classroom_id").annotate(n=Count("id")))
    rows = [{"class": c.name, "grade": c.grade.name, "field": c.study_field.name if c.study_field else "",
             "capacity": c.capacity or "", "students": counts.get(c.pk, 0), "courses": courses.get(c.pk, 0),
             "homeroom": _name(c.homeroom_teacher) if c.homeroom_teacher else ""} for c in qs.order_by("grade__order", "name")]
    return {"columns": [("class", "کلاس"), ("grade", "پایه"), ("field", "رشته"), ("capacity", "ظرفیت"),
                        ("students", "دانش‌آموزان"), ("courses", "درس‌ها"), ("homeroom", "معلم راهنما")],
            "rows": rows, "totals": {"classes": len(rows), "students": sum(r["students"] for r in rows)}}


def report_teachers(user, org, classroom=None, **kw):
    school = _one_school(user, org)
    members = User.objects.filter(school_memberships__school=school, role="teacher").distinct() | \
        User.objects.filter(role="teacher", org=school)
    members = members.distinct().order_by("last_name", "username")
    rows = []
    for t in members:
        cs = Course.objects.filter(org=school, teacher=t)
        if classroom is not None:
            cs = cs.filter(classroom=classroom)
        rows.append({"teacher": _name(t), "username": t.username, "courses": cs.count(),
                     "classes": cs.exclude(classroom=None).values("classroom").distinct().count(),
                     "titles": "، ".join(sorted({c.title for c in cs})[:10])})
    rows = [r for r in rows if classroom is None or r["courses"]]
    return {"columns": [("teacher", "معلم"), ("username", "کد کاربری"), ("courses", "درس‌ها"), ("classes", "کلاس‌ها"),
                        ("titles", "عنوان درس‌ها")], "rows": rows,
            "totals": {"teachers": len(rows), "courses": sum(r["courses"] for r in rows)}}


def report_students(user, org, classroom=None, **kw):
    school = _one_school(user, org)
    qs = User.objects.filter(role="student", org=school).order_by("last_name", "first_name", "username")
    if classroom is not None:
        qs = qs.filter(classrooms=classroom)
    cls = {}
    for c in ClassRoom.objects.filter(school=school, academic_year__is_current=True).values("students", "name"):
        if c["students"]:
            cls.setdefault(c["students"], []).append(c["name"])
    rows = [{"student": _name(s), "student_code": s.student_code or "", "national_id": s.national_id or "",
             "class": "، ".join(cls.get(s.pk, [])), "gender": {"boy": "پسر", "girl": "دختر"}.get(s.gender, "")} for s in qs[:2000]]
    return {"columns": [("student", "دانش‌آموز"), ("student_code", "کد دانش‌آموزی"), ("national_id", "کد ملی"),
                        ("class", "کلاس"), ("gender", "جنسیت")], "rows": rows, "totals": {"students": len(rows)}}


def report_grades(user, org, classroom=None, **kw):
    """Average percent per course (grouped by class) and per student across the school's submitted exams."""
    school = _one_school(user, org)
    att = Attempt.objects.filter(exam__course__org=school, submitted_at__isnull=False, total__gt=0)
    if classroom is not None:
        att = att.filter(exam__course__classroom=classroom)
    data = att.values("exam__course_id", "exam__course__title", "exam__course__classroom__name", "score", "total")
    by_course = defaultdict(list)
    for d in data:
        by_course[(d["exam__course__title"], d["exam__course__classroom__name"] or "")].append(float(d["score"]) / float(d["total"]) * 100)
    rows = [{"course": k[0], "class": k[1], "attempts": len(v), "average": round(sum(v) / len(v), 1)}
            for k, v in sorted(by_course.items())]
    return {"columns": [("course", "درس"), ("class", "کلاس"), ("attempts", "شرکت‌ها"), ("average", "میانگین (٪)")],
            "rows": rows, "totals": {"attempts": sum(r["attempts"] for r in rows)}}


def report_student_grades(user, org, classroom=None, **kw):
    school = _one_school(user, org)
    att = Attempt.objects.filter(exam__course__org=school, submitted_at__isnull=False, total__gt=0)
    if classroom is not None:
        att = att.filter(exam__course__classroom=classroom)
    per = defaultdict(list)
    for d in att.values("student_id", "student__first_name", "student__last_name", "student__username", "score", "total"):
        per[(d["student_id"], f'{d["student__first_name"]} {d["student__last_name"]}'.strip() or d["student__username"])].append(
            float(d["score"]) / float(d["total"]) * 100)
    rows = [{"student": k[1], "exams": len(v), "average": round(sum(v) / len(v), 1)} for k, v in sorted(per.items(), key=lambda x: x[0][1])]
    return {"columns": [("student", "دانش‌آموز"), ("exams", "آزمون‌ها"), ("average", "میانگین (٪)")], "rows": rows,
            "totals": {"students": len(rows)}}


TEACHER_ROLES = ("teacher",)


def _range(kw):
    from datetime import date, timedelta
    from django.utils import timezone
    today = timezone.localdate()
    try:
        d1 = date.fromisoformat(kw.get("date_from") or "") if kw.get("date_from") else today - timedelta(days=30)
        d2 = date.fromisoformat(kw.get("date_to") or "") if kw.get("date_to") else today
    except ValueError:
        raise ValueError("تاریخ نامعتبر است.")
    if d2 < d1 or (d2 - d1).days > 400:
        raise ValueError("بازه تاریخ نامعتبر است (حداکثر ۴۰۰ روز).")
    return d1, d2


def _rate(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def report_absence_students(user, org, classroom=None, **kw):
    from apps.attendance.models import AttendanceRecord
    school = _one_school(user, org)
    d1, d2 = _range(kw)
    qs = AttendanceRecord.objects.filter(classroom__school=school, date__range=(d1, d2))
    if classroom is not None:
        qs = qs.filter(classroom=classroom)
    rows = []
    for d in (qs.values("student_id", "student__first_name", "student__last_name", "student__username")
              .annotate(total=Count("id"), absent=Count("id", filter=Q(status="absent")),
                        late=Count("id", filter=Q(status="late")),
                        excused=Count("id", filter=Q(status__in=["excused", "leave"])))
              .order_by("student__last_name", "student__username")[:2000]):
        name = f'{d["student__first_name"]} {d["student__last_name"]}'.strip() or d["student__username"]
        rows.append({"student": name, "days": d["total"], "absent": d["absent"], "late": d["late"],
                     "excused": d["excused"], "rate": _rate(d["absent"], d["total"])})
    return {"columns": [("student", "دانش‌آموز"), ("days", "روز ثبت‌شده"), ("absent", "غیبت"), ("late", "تأخیر"),
                        ("excused", "موجه / مرخصی"), ("rate", "درصد غیبت")], "rows": rows,
            "totals": {"students": len(rows), "absent": sum(r["absent"] for r in rows)}}


def report_attendance_classes(user, org, classroom=None, **kw):
    from apps.attendance.models import AttendanceRecord
    school = _one_school(user, org)
    d1, d2 = _range(kw)
    qs = AttendanceRecord.objects.filter(classroom__school=school, date__range=(d1, d2))
    if classroom is not None:
        qs = qs.filter(classroom=classroom)
    rows = []
    for d in qs.values("classroom__name").annotate(total=Count("id"), present=Count("id", filter=Q(status__in=["present", "late"])),
                                                    absent=Count("id", filter=Q(status="absent"))).order_by("classroom__name"):
        rows.append({"class": d["classroom__name"], "records": d["total"], "present": d["present"], "absent": d["absent"],
                     "rate": _rate(d["present"], d["total"])})
    return {"columns": [("class", "کلاس"), ("records", "ثبت‌ها"), ("present", "حاضر"), ("absent", "غایب"),
                        ("rate", "درصد حضور")], "rows": rows, "totals": {"records": sum(r["records"] for r in rows)}}


def report_teacher_attendance(user, org, classroom=None, **kw):
    from apps.attendance.models import TeacherAttendance
    school = _one_school(user, org)
    d1, d2 = _range(kw)
    rows = []
    for d in (TeacherAttendance.objects.filter(school=school, date__range=(d1, d2)).values(
            "teacher_id", "teacher__first_name", "teacher__last_name", "teacher__username")
            .annotate(days=Count("id"), present=Count("id", filter=Q(status__in=["present", "late"])),
                      absent=Count("id", filter=Q(status="absent")), leave=Count("id", filter=Q(status="leave")))
            .order_by("teacher__last_name", "teacher__username")):
        name = f'{d["teacher__first_name"]} {d["teacher__last_name"]}'.strip() or d["teacher__username"]
        rows.append({"teacher": name, "days": d["days"], "present": d["present"], "absent": d["absent"],
                     "leave": d["leave"], "rate": _rate(d["present"], d["days"])})
    return {"columns": [("teacher", "معلم"), ("days", "روز ثبت‌شده"), ("present", "حاضر"), ("absent", "غایب"),
                        ("leave", "مرخصی"), ("rate", "درصد حضور")], "rows": rows,
            "totals": {"teachers": len(rows), "absent": sum(r["absent"] for r in rows)}}


def _assignment_rows(assignments):
    from apps.coursework.models import Submission
    from apps.courses.models import Enrollment
    ids = [a.pk for a in assignments]
    subs = {r["assignment_id"]: r for r in Submission.objects.filter(assignment_id__in=ids).values("assignment_id")
            .annotate(n=Count("id"), late=Count("id", filter=Q(is_late=True)), graded=Count("id", filter=Q(points__isnull=False)),
                      avg=Avg("points"))}
    rows = []
    for a in assignments:
        if a.audience == "selected":
            expected = a.targets.count()
        else:
            expected = Enrollment.objects.filter(course=a.course).count()
        s = subs.get(a.pk, {})
        rows.append({"assignment": a.title, "course": a.course.title, "class": a.course.classroom.name if a.course.classroom_id else "",
                     "due": a.due_at.date().isoformat(), "expected": expected, "submitted": s.get("n", 0),
                     "late": s.get("late", 0), "graded": s.get("graded", 0),
                     "submit_rate": _rate(s.get("n", 0), expected), "average": round(float(s["avg"]), 1) if s.get("avg") is not None else None})
    return rows


ASSIGN_COLUMNS = [("assignment", "تکلیف"), ("course", "درس"), ("class", "کلاس"), ("due", "مهلت"), ("expected", "باید ارسال کنند"),
                  ("submitted", "ارسال‌شده"), ("late", "با تأخیر"), ("graded", "تصحیح‌شده"), ("submit_rate", "درصد ارسال"),
                  ("average", "میانگین نمره")]


def report_assignments_school(user, org, classroom=None, **kw):
    from apps.coursework.models import Assignment
    school = _one_school(user, org)
    qs = Assignment.objects.filter(course__org=school).select_related("course__classroom").order_by("-due_at")
    if classroom is not None:
        qs = qs.filter(course__classroom=classroom)
    rows = _assignment_rows(list(qs[:500]))
    return {"columns": ASSIGN_COLUMNS, "rows": rows, "totals": {"assignments": len(rows), "submitted": sum(r["submitted"] for r in rows)}}


def report_my_assignments(user, org=None, classroom=None, **kw):
    from apps.coursework.models import Assignment
    qs = Assignment.objects.filter(course__teacher=user).select_related("course__classroom").order_by("-due_at")
    if classroom is not None:
        qs = qs.filter(course__classroom=classroom)
    rows = _assignment_rows(list(qs[:500]))
    return {"columns": ASSIGN_COLUMNS, "rows": rows, "totals": {"assignments": len(rows), "submitted": sum(r["submitted"] for r in rows)}}


def report_my_attendance(user, org=None, classroom=None, **kw):
    from apps.attendance.models import TeacherAttendance
    d1, d2 = _range(kw)
    qs = TeacherAttendance.objects.filter(teacher=user, date__range=(d1, d2)).select_related("school").order_by("date")
    rows = [{"date": r.date.isoformat(), "school": r.school.name, "status": r.get_status_display(), "note": r.note} for r in qs[:1000]]
    absent = sum(1 for r in qs if r.status == "absent")
    return {"columns": [("date", "تاریخ"), ("school", "مدرسه"), ("status", "وضعیت"), ("note", "توضیح")], "rows": rows,
            "totals": {"days": len(rows), "absent": absent}}


def _student_classes(school):
    from apps.academics.models import ClassRoom as CR
    out = {}
    for c in CR.objects.filter(school=school, academic_year__is_current=True).values("students", "name"):
        if c["students"]:
            out.setdefault(c["students"], []).append(c["name"])
    return out


def report_evaluation_students(user, org, classroom=None, **kw):
    from apps.evaluation.logic import course_percentages, courses_with_final, evaluate_students, policy_for, weights_for
    school = _one_school(user, org)
    course_ids = None
    if classroom is not None:
        course_ids = list(Course.objects.filter(classroom=classroom).values_list("pk", flat=True))
    pct = course_percentages([school.pk], course_ids=course_ids)
    _, pass_mark, bands = policy_for(school)
    finals = courses_with_final([school.pk])
    classes = _student_classes(school)
    names = {u.pk: _name(u) for u in User.objects.filter(pk__in=[k[0] for k in pct])}
    rows = []
    for r in sorted(evaluate_students(school, pct, pass_mark, bands, finals=finals, weights=weights_for(school)),
                    key=lambda x: names.get(x["student_id"], "")):
        rows.append({"student": names.get(r["student_id"], ""), "class": "، ".join(classes.get(r["student_id"], [])),
                     "courses": r["courses"], "failed": r["failed_courses"], "failed_list": r["failed_list"],
                     "average": r["average"], "band": r["band"], "status": r["status"]})
    return {"columns": [("student", "دانش‌آموز"), ("class", "کلاس"), ("courses", "درس‌ها"), ("failed", "درس‌های مردود"),
                        ("failed_list", "نام درس‌های مردود"), ("average", "معدل (٪)"), ("band", "ارزشیابی"), ("status", "وضعیت")],
            "rows": rows, "totals": {"students": len(rows), "passed": sum(1 for r in rows if r["status"] == "قبول"),
                                     "pass_mark": pass_mark}}


def report_evaluation_classes(user, org, classroom=None, **kw):
    from apps.evaluation.logic import course_percentages, evaluate_students, policy_for
    school = _one_school(user, org)
    _, pass_mark, bands = policy_for(school)
    pct = course_percentages([school.pk])
    from apps.evaluation.logic import courses_with_final, weights_for
    evald = {r["student_id"]: r for r in evaluate_students(school, pct, pass_mark, bands,
                                                           finals=courses_with_final([school.pk]), weights=weights_for(school))}
    per_class = defaultdict(list)
    for cname, sids in _class_members(school).items():
        for sid in sids:
            if sid in evald:
                per_class[cname].append(evald[sid])
    rows = []
    for cname, items in sorted(per_class.items()):
        if classroom is not None and cname != classroom.name:
            continue
        avg = sum(i["average"] for i in items) / len(items)
        rows.append({"class": cname, "evaluated": len(items), "average": round(avg, 1),
                     "passed": sum(1 for i in items if i["status"] == "قبول"),
                     "pass_rate": _rate(sum(1 for i in items if i["status"] == "قبول"), len(items)),
                     "bands": "، ".join(f"{b}: {sum(1 for i in items if i['band'] == b)}" for b in [x["label"] for x in bands])})
    return {"columns": [("class", "کلاس"), ("evaluated", "دانش‌آموزان ارزیابی‌شده"), ("average", "میانگین کلاس (٪)"),
                        ("passed", "قبول"), ("pass_rate", "درصد قبولی"), ("bands", "توزیع ارزشیابی")],
            "rows": rows, "totals": {"classes": len(rows), "pass_mark": pass_mark}}


def _class_members(school):
    from apps.academics.models import ClassRoom as CR
    out = {}
    for c in CR.objects.filter(school=school, academic_year__is_current=True).prefetch_related("students"):
        out[c.name] = [s.pk for s in c.students.all()]
    return out


def report_evaluation_schools(user, org, classroom=None, **kw):
    from apps.evaluation.logic import course_percentages, evaluate_students, policy_for
    rows = []
    for sid in _school_ids(user, org, kw.get("filters"))[:400]:
        school = OrgUnit.objects.get(pk=sid)
        pct = course_percentages([sid])
        _, pass_mark, bands = policy_for(school)
        from apps.evaluation.logic import courses_with_final, weights_for
        items = evaluate_students(school, pct, pass_mark, bands, finals=courses_with_final([sid]), weights=weights_for(school))
        if not items:
            continue
        passed = sum(1 for i in items if i["status"] == "قبول")
        rows.append({"school": school.name, "evaluated": len(items),
                     "average": round(sum(i["average"] for i in items) / len(items), 1),
                     "pass_rate": _rate(passed, len(items)),
                     "bands": "، ".join(f"{b['label']}: {sum(1 for i in items if i['band'] == b['label'])}" for b in bands)})
    rows.sort(key=lambda r: r["school"])
    return {"columns": [("school", "مدرسه"), ("evaluated", "دانش‌آموزان ارزیابی‌شده"), ("average", "میانگین (٪)"),
                        ("pass_rate", "درصد قبولی"), ("bands", "توزیع ارزشیابی")], "rows": rows,
            "totals": {"schools": len(rows), "evaluated": sum(r["evaluated"] for r in rows)}}


def _rank_context(school):
    from apps.evaluation.ranking import _ancestor
    return _ancestor(school, "region"), _ancestor(school, "province")


def report_ranking_overall(user, org, classroom=None, **kw):
    from apps.evaluation.ranking import ranks_for_school_students
    school = _one_school(user, org)
    region, province = _rank_context(school)
    ranks, _ = ranks_for_school_students(school, region, province)
    classes = {c.pk: c.name for c in ClassRoom.objects.filter(school=school, academic_year__is_current=True)}
    names = {u.pk: _name(u) for u in User.objects.filter(pk__in=list(ranks.keys()))}
    rows = []
    for sid, r in ranks.items():
        if classroom is not None and r["class_id"] != classroom.pk:
            continue
        rows.append({"student": names.get(sid, ""), "class": classes.get(r["class_id"], ""), "average": r["overall"],
                     "class_rank": r["class_rank"], "school_rank": r["school_rank"], "region_rank": r["region_rank"],
                     "province_rank": r["province_rank"]})
    rows.sort(key=lambda x: (x["school_rank"] or 10**9, x["student"]))
    return {"columns": [("student", "دانش‌آموز"), ("class", "کلاس"), ("average", "معدل (٪)"), ("class_rank", "رتبه در کلاس"),
                        ("school_rank", "رتبه در مدرسه"), ("region_rank", "رتبه در منطقه"), ("province_rank", "رتبه در استان")],
            "rows": rows, "totals": {"ranked": len(rows)}}


def report_ranking_course(user, org, classroom=None, **kw):
    from apps.evaluation.ranking import course_ranks, _school_grades
    school = _one_school(user, org)
    region, province = _rank_context(school)
    own = _school_grades(school)
    course_ids = sorted({cid for d in own.values() for cid in d["courses"]})
    if classroom is not None:
        course_ids = [c for c in course_ids if Course.objects.filter(pk=c, classroom=classroom).exists()]
    names = {u.pk: _name(u) for u in User.objects.filter(pk__in=list(own.keys()))}
    titles = {}
    for d in own.values():
        titles.update(d["titles"])
    rows = []
    for cid in course_ids:
        grades, ranks = course_ranks(school, region, province, cid)
        for sid, g in sorted(grades.items(), key=lambda x: ranks["school"].get(x[0], 10**9)):
            rows.append({"course": titles.get(cid, ""), "student": names.get(sid, ""), "grade": round(g, 1),
                         "class_rank": ranks["class"].get(sid), "school_rank": ranks["school"].get(sid),
                         "region_rank": ranks["region"].get(sid), "province_rank": ranks["province"].get(sid)})
    return {"columns": [("course", "درس"), ("student", "دانش‌آموز"), ("grade", "نمره درس"), ("class_rank", "رتبه در کلاس"),
                        ("school_rank", "رتبه در مدرسه"), ("region_rank", "رتبه در منطقه"), ("province_rank", "رتبه در استان")],
            "rows": rows, "totals": {"rows": len(rows)}}


def report_ranking_district(user, org, classroom=None, **kw):
    from apps.evaluation.ranking import _ancestor, province_table
    if org is None:
        raise ValueError("ناحیه یا استان را انتخاب کنید.")
    table = province_table(org)
    unit_ranks = competition_ranks({sid: d["overall"] for sid, d in table.items() if d["overall"] is not None})
    prov = _ancestor(org, "province") or (org if org.kind == "province" else None)
    prov_ranks = {}
    if prov is not None:
        ptable = province_table(prov)
        prov_ranks = competition_ranks({sid: d["overall"] for sid, d in ptable.items() if d["overall"] is not None})
    names = {u.pk: _name(u) for u in User.objects.filter(pk__in=list(table.keys()))}
    schools = {s.pk: s.name for s in OrgUnit.objects.filter(pk__in={d["school"] for d in table.values()})}
    rows = [{"student": names.get(sid, ""), "school": schools.get(d["school"], ""), "average": round(d["overall"], 1),
             "unit_rank": unit_ranks.get(sid), "province_rank": prov_ranks.get(sid)}
            for sid, d in table.items() if d["overall"] is not None]
    rows.sort(key=lambda x: (x["unit_rank"] or 10**9, x["student"]))
    return {"columns": [("student", "دانش‌آموز"), ("school", "مدرسه"), ("average", "معدل (٪)"),
                        ("unit_rank", "رتبه در این محدوده"), ("province_rank", "رتبه در استان")],
            "rows": rows[:2000], "totals": {"ranked": len(rows)}}


def report_directory(user, org, classroom=None, **kw):
    """Every school with its full profile, narrowed by the complete filter set."""
    schools = list(_filtered_schools(user, org, kw.get("filters")).order_by("name")[:3000])
    ids = [s.pk for s in schools]
    students = dict(User.objects.filter(role="student", org_id__in=ids).values_list("org_id").annotate(n=Count("id")))
    teachers = dict(SchoolMembership.objects.filter(school_id__in=ids, user__role="teacher").values_list("school_id").annotate(n=Count("user", distinct=True)))
    classes = dict(ClassRoom.objects.filter(school_id__in=ids, academic_year__is_current=True).values_list("school_id").annotate(n=Count("id")))
    profiles = {p.school_id: p for p in SchoolProfile.objects.select_related("level", "county").filter(school_id__in=ids)}

    def province_of(o):
        node = o
        while node is not None:
            if node.kind == "province":
                return node.name
            node = node.parent
        return ""
    rows = []
    for s in schools:
        p = profiles.get(s.pk)
        rows.append({"school": s.name, "code": p.school_code if p else "", "province": province_of(s.parent) if s.parent_id else "",
                     "county": p.county.name if p and p.county_id else "", "region": s.parent.name if s.parent_id and s.parent.kind == "region" else "",
                     "level": p.level.name if p else "", "type": p.get_school_type_display() if p else "", "shift": p.get_shift_display() if p else "",
                     "ownership": p.get_ownership_display() if p else "", "gender": p.get_gender_display() if p else "",
                     "classes": classes.get(s.pk, 0), "students": students.get(s.pk, 0), "teachers": teachers.get(s.pk, 0)})
    return {"columns": [("school", "مدرسه"), ("code", "کد آموزشگاه"), ("province", "استان"), ("county", "شهرستان"), ("region", "ناحیه/منطقه"),
                        ("level", "مقطع"), ("type", "نوع"), ("shift", "شیفت"), ("ownership", "مالکیت"), ("gender", "جنسیت مدرسه"),
                        ("classes", "کلاس‌ها"), ("students", "دانش‌آموزان"), ("teachers", "معلمان")],
            "rows": rows, "totals": {"schools": len(rows), "students": sum(r["students"] for r in rows),
                                     "teachers": sum(r["teachers"] for r in rows), "classes": sum(r["classes"] for r in rows)}}


def report_summary(user, org, classroom=None, **kw):
    """Totals grouped by region (default), county, province or level — with the same filters."""
    key = {"region": "region", "county": "county", "province": "province", "level": "level"}.get(kw.get("group") or "region", "region")
    d = report_directory(user, org, **kw)
    acc = {}
    for r in d["rows"]:
        g = r[key] or "—"
        a = acc.setdefault(g, {"group": g, "schools": 0, "classes": 0, "students": 0, "teachers": 0})
        a["schools"] += 1
        a["classes"] += r["classes"]
        a["students"] += r["students"]
        a["teachers"] += r["teachers"]
    label = {"region": "ناحیه/منطقه", "county": "شهرستان", "province": "استان", "level": "مقطع"}[key]
    return {"columns": [("group", label), ("schools", "مدارس"), ("classes", "کلاس‌ها"), ("students", "دانش‌آموزان"), ("teachers", "معلمان")],
            "rows": sorted(acc.values(), key=lambda x: x["group"]), "totals": d["totals"]}


def report_exams_school(user, org, classroom=None, **kw):
    from apps.courses.models import Enrollment
    from apps.evaluation.logic import policy_for
    from apps.exams.models import Exam
    school = _one_school(user, org)
    _, pass_mark, _b = policy_for(school)
    exams = Exam.objects.filter(course__org=school).select_related("course__classroom").order_by("-starts_at")
    if classroom is not None:
        exams = exams.filter(course__classroom=classroom)
    rows = []
    for e in exams[:500]:
        pcts = [float(a.score) / float(a.total) * 100
                for a in Attempt.objects.filter(exam=e, submitted_at__isnull=False, total__gt=0).only("score", "total")]
        rows.append({"exam": e.title, "course": e.course.title, "class": e.course.classroom.name if e.course.classroom_id else "",
                     "type": e.get_assessment_display(), "starts": e.starts_at.date().isoformat(),
                     "published": "بله" if e.is_published else "خیر", "enrolled": Enrollment.objects.filter(course=e.course).count(),
                     "attempts": len(pcts), "average": round(sum(pcts) / len(pcts), 1) if pcts else None,
                     "pass_rate": _rate(sum(1 for x in pcts if x >= pass_mark), len(pcts))})
    return {"columns": [("exam", "آزمون"), ("course", "درس"), ("class", "کلاس"), ("type", "نوع"), ("starts", "تاریخ شروع"),
                        ("published", "منتشرشده"), ("enrolled", "دانش‌آموزان درس"), ("attempts", "شرکت‌کنندگان"),
                        ("average", "میانگین (٪)"), ("pass_rate", "درصد قبولی")], "rows": rows, "totals": {"exams": len(rows)}}


def report_exams_district(user, org, classroom=None, **kw):
    from apps.exams.models import Exam
    rows = []
    for s in _filtered_schools(user, org, kw.get("filters")).order_by("name")[:400]:
        exams = Exam.objects.filter(course__org=s)
        if not exams.exists():
            continue
        pcts = [float(a.score) / float(a.total) * 100
                for a in Attempt.objects.filter(exam__course__org=s, submitted_at__isnull=False, total__gt=0).only("score", "total")[:20000]]
        rows.append({"school": s.name, "exams": exams.count(), "published": exams.filter(is_published=True).count(),
                     "attempts": len(pcts), "average": round(sum(pcts) / len(pcts), 1) if pcts else None})
    return {"columns": [("school", "مدرسه"), ("exams", "آزمون‌ها"), ("published", "منتشرشده"), ("attempts", "شرکت‌ها"),
                        ("average", "میانگین (٪)")], "rows": rows, "totals": {"schools": len(rows)}}


REPORTS = {
    "directory": {"label": "فهرست کامل مدارس با فیلتر (استان، شهرستان، ناحیه، مقطع، نوع، شیفت، مالکیت، جنسیت)", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_directory},
    "summary": {"label": "آمار تجمیعی بر اساس ناحیه / شهرستان / استان / مقطع (با فیلتر)", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_summary},
    "exams_school": {"label": "آزمون‌های مدرسه: شرکت‌کنندگان، میانگین، قبولی", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_exams_school},
    "exams_district": {"label": "آزمون‌های مدارس محدوده (با فیلتر)", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_exams_district},
    "district": {"label": "مدارس منطقه: کلاس‌ها، دانش‌آموزان، معلمان، رشته‌ها", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_district},
    "fields": {"label": "رشته‌ها در منطقه (کلاس و دانش‌آموز)", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_fields},
    "classes": {"label": "کلاس‌های مدرسه", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_classes},
    "teachers": {"label": "معلمان مدرسه و درس‌هایشان", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_teachers},
    "students": {"label": "فهرست دانش‌آموزان مدرسه", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_students},
    "grades": {"label": "نمرات: میانگین هر درس و کلاس", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_grades},
    "student_grades": {"label": "نمرات: میانگین هر دانش‌آموز", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_student_grades},
    "evaluation_students": {"label": "ارزشیابی تحصیلی دانش‌آموزان (بر اساس نمرات)", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_evaluation_students},
    "evaluation_classes": {"label": "ارزشیابی تحصیلی کلاس‌ها", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_evaluation_classes},
    "evaluation_schools": {"label": "ارزشیابی تحصیلی مدارس منطقه", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_evaluation_schools},
    "ranking_overall": {"label": "رتبه‌بندی دانش‌آموزان: کلاس، مدرسه، منطقه، استان", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_ranking_overall},
    "ranking_course": {"label": "رتبه‌بندی دانش‌آموزان در هر درس", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_ranking_course},
    "ranking_district": {"label": "رتبه‌بندی دانش‌آموزان محدوده (منطقه / استان)", "scope": "district", "roles": DISTRICT_ROLES, "fn": report_ranking_district},
    "absence_students": {"label": "غیبت و حضور دانش‌آموزان (بازه تاریخ)", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_absence_students},
    "attendance_classes": {"label": "درصد حضور هر کلاس (بازه تاریخ)", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_attendance_classes},
    "teacher_attendance": {"label": "حضور و غیاب معلمان مدرسه (بازه تاریخ)", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_teacher_attendance},
    "assignments_school": {"label": "تکالیف مدرسه: ارسال، تأخیر، نمره", "scope": "school", "roles": SCHOOL_ROLES, "fn": report_assignments_school},
    "my_assignments": {"label": "تکالیف من: ارسال و تصحیح", "scope": "teacher", "roles": TEACHER_ROLES, "fn": report_my_assignments},
    "my_attendance": {"label": "حضور و غیاب من در مدرسه", "scope": "teacher", "roles": TEACHER_ROLES, "fn": report_my_attendance},
}


def available(user):
    return [{"key": k, "label": v["label"], "scope": v["scope"]} for k, v in REPORTS.items() if user.role in v["roles"]]
