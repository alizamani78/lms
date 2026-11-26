"""Validation and writing for bulk imports. Every rule runs on the server; the browser preview is only a view."""
from django.db import IntegrityError, transaction

from apps.academics.models import AcademicYear, ClassRoom, Grade, StudyField, Subject
from apps.accounts.models import SchoolMembership, User
from apps.core.persian import canonical, is_valid_national_id
from apps.courses.models import Course, Enrollment


class NoCurrentYear(Exception):
    pass


def _resolve_class(ctx, class_value, grade_name=""):
    """The classroom a row refers to. The class column may be a title («رودکی 802») or a code («6»). A code is
    read together with the grade (primary schools number classes inside every grade)."""
    value = (class_value or "").strip()
    if not value:
        return None
    grade = ctx["grades"].get(canonical(grade_name)) if grade_name else None
    if grade is not None:
        hit = ctx["classes_by_grade_code"].get((grade.pk, value))
        if hit is not None:
            return hit
    return ctx["classes"].get(canonical(value)) or (ctx["classes_by_code"].get(value) if value in ctx["classes_by_code"] else None)


def _grades_for_school(school):
    """The grades a school may use: those of its own level (so «دهم» means the right level); all grades if no profile."""
    from apps.accounts.models import SchoolProfile
    level = SchoolProfile.objects.filter(school=school).values_list("level_id", flat=True).first()
    return Grade.objects.filter(level_id=level) if level else Grade.objects.all()


def _context(kind, school, year, rows):
    vals = [r["values"] for r in rows]
    nids = {v.get("national_id") or v.get("teacher_national_id") for v in vals} - {"", None}
    codes = {v.get("student_code") for v in vals} - {"", None}
    names = nids | codes
    teachers = {u.national_id: u for u in User.objects.filter(role=User.Role.TEACHER, national_id__in=nids)}
    members = set(SchoolMembership.objects.filter(school=school, user__in=teachers.values())
                  .values_list("user_id", flat=True))
    return {
        "year": year, "school": school, "seen": {},
        "classes": {canonical(c.name): c for c in ClassRoom.objects.filter(school=school, academic_year=year)},
        "classes_by_code": {c.code: c for c in ClassRoom.objects.filter(school=school, academic_year=year).exclude(code="")},
        "classes_by_grade_code": {(c.grade_id, c.code): c for c in ClassRoom.objects.filter(school=school, academic_year=year).exclude(code="")},
        "subjects_by_code": {x.code: x for x in Subject.objects.all()},
        "class_codes": set(ClassRoom.objects.filter(school=school).exclude(code="").values_list("code", flat=True)),
        "grades": {canonical(g.name): g for g in _grades_for_school(school)},
        "fields": {canonical(f.name): f for f in StudyField.objects.all()},
        "fields_by_code": {f.code: f for f in StudyField.objects.exclude(code=None)},
        "subject_codes": set(Subject.objects.values_list("code", flat=True)),
        "db_codes": _stored_codes(codes),
        "db_nids": _stored_nids(nids, codes),
        "db_usernames": set(User.objects.filter(username__in=names).values_list("username", flat=True)),
        "teachers": teachers, "teacher_members": members,
        "course_keys": {(canonical(c.title), c.classroom_id) for c in Course.objects.filter(org=school)},
    }


def _dup(ctx, key, n):
    if key in ctx["seen"]:
        return [f"تکراری در همین فایل (ردیف {ctx['seen'][key]})"]
    ctx["seen"][key] = n
    return []


def _required(v, fields):
    return [f"{label} الزامی است" for f, label in fields if not v.get(f)]


GENDER_WORDS = {"پسر": "boy", "دختر": "girl", "boy": "boy", "girl": "girl"}



def _padded(code):
    code = (code or "").strip()
    return code.zfill(10) if code.isdigit() and len(code) < 10 else code


def _stored_codes(codes):
    """The student codes already in the system, compared in their padded form (old 9-digit codes match a padded one)."""
    padded = {_padded(c) for c in codes if c}
    lookup = list(padded | {c.lstrip("0") for c in padded if c} | {c for c in codes if c})
    return {_padded(u) for u in User.objects.filter(student_code__in=lookup).values_list("student_code", flat=True) if u}


def _stored_nids(nids, codes):
    """National ids already in the system, including a valid padded student code (it becomes the national id)."""
    cand = set(nids) | {_padded(c) for c in codes if c and len(_padded(c)) == 10 and is_valid_national_id(_padded(c))}
    return set(User.objects.filter(national_id__in=list(cand)).values_list("national_id", flat=True))

def _split_code(v):
    """A student code shorter than 10 digits gets leading zeros up to 10 digits. A 10-digit code that passes the
    national-id check is the national id (when the row has none)."""
    code, nid = (v.get("student_code", "") or "").strip(), v.get("national_id", "")
    if code.isdigit() and len(code) < 10:
        code = code.zfill(10)
    v["student_code"] = code
    if len(code) == 10 and code.isdigit() and is_valid_national_id(code) and not nid:
        v["national_id"] = code
    return v


def check_students(v, ctx, n):
    from apps.accounts.models import SchoolProfile, gender_allowed
    _split_code(v)
    v["_missing_class"] = ""
    errs = _required(v, [("first_name", "نام"), ("last_name", "نام خانوادگی")])
    if not v.get("student_code") and not v.get("national_id"):
        errs.append("کد دانش‌آموز یا کد ملی لازم است")
    if v.get("gender"):
        g = GENDER_WORDS.get(canonical(v["gender"]))
        if g is None:
            errs.append("جنسیت باید «پسر» یا «دختر» باشد")
        else:
            v["gender"] = g
    profile = ctx.setdefault("profile", SchoolProfile.objects.select_related("level").filter(school=ctx["school"]).first())
    from apps.accounts.models import school_gender_for
    if school_gender_for(profile):
        v["gender"] = school_gender_for(profile)          # boys-only / girls-only school: no gender to enter
    if profile is not None and profile.level.code in ("MS2", "KAR", "FNI") and profile.gender != "mixed":
        if not v.get("gender"):
            errs.append("جنسیت دانش‌آموز برای این مدرسه لازم است")
        elif not gender_allowed(v["gender"], profile):
            errs.append("جنسیت دانش‌آموز با نوع مدرسه (پسرانه/دخترانه) هم‌خوان نیست")
    code, nid = v.get("student_code", ""), v.get("national_id", "")
    if code and not (code.isdigit() and len(code) == 10):
        errs.append("کد دانش‌آموز باید عددی باشد (کمتر از ۱۰ رقم با صفر تکمیل می‌شود)")
    elif code:
        if code in ctx["db_codes"]:
            errs.append("کد دانش‌آموز قبلاً در سامانه ثبت شده است")
        errs += _dup(ctx, ("code", code), n)
    if nid:
        if not is_valid_national_id(nid):
            errs.append("کد ملی نامعتبر است (کنترل الگوریتم)")
        elif nid in ctx["db_nids"]:
            errs.append("کد ملی قبلاً ثبت شده است")
        else:
            errs += _dup(ctx, ("nid", nid), n)
    if code and code in ctx["db_usernames"]:
        errs.append("نام کاربری (کد دانش‌آموز) در سامانه وجود دارد")

    return errs


def check_teachers(v, ctx, n):
    errs = _required(v, [("first_name", "نام"), ("last_name", "نام خانوادگی"), ("national_id", "کد ملی")])
    nid = v.get("national_id", "")
    if nid and not is_valid_national_id(nid):
        errs.append("کد ملی نامعتبر است (کنترل الگوریتم)")
    elif nid:
        if nid in ctx["db_nids"]:
            errs.append("کد ملی قبلاً ثبت شده است")
        errs += _dup(ctx, ("nid", nid), n)
    if v.get("mobile") and not (v["mobile"].isdigit() and v["mobile"].startswith("09") and len(v["mobile"]) == 11):
        errs.append("شماره موبایل باید با 09 شروع و ۱۱ رقم باشد")
    return errs


def check_classes(v, ctx, n):
    errs = _required(v, [("title", "عنوان کلاس"), ("grade", "پایه")])
    if v.get("title") and canonical(v["title"]) in ctx["classes"]:
        errs.append("کلاسی با همین عنوان در این مدرسه و سال جاری وجود دارد")
    elif v.get("title"):
        errs += _dup(ctx, ("title", canonical(v["title"])), n)
    if v.get("grade") and canonical(v["grade"]) not in ctx["grades"]:
        errs.append(f"پایه «{v['grade']}» در کاتالوگ پیدا نشد")
    if v.get("field_code") and v["field_code"] not in ctx["fields_by_code"] and not v.get("field"):
        errs.append(f"رشته با کد {v['field_code']} در کاتالوگ نیست و نام رشته برای ساختن آن در فایل نیست")
    elif v.get("field") and canonical(v["field"]) not in ctx["fields"] and not v.get("field_code"):
        errs.append(f"رشته «{v['field']}» در کاتالوگ پیدا نشد")
    code = v.get("code", "")
    if code:
        g = ctx["grades"].get(canonical(v.get("grade", "")))
        if g is not None and (g.pk, code) in ctx["classes_by_grade_code"]:
            errs.append("کد کلاس در این پایه و مدرسه تکراری است")
        errs += _dup(ctx, ("code", v.get("grade", ""), code), n)
    if v.get("capacity") and not v["capacity"].isdigit():
        errs.append("ظرفیت باید عدد باشد")
    return errs


def check_courses(v, ctx, n):
    errs = _required(v, [("title", "نام درس"), ("class_title", "کلاس"), ("teacher_national_id", "کد ملی معلم")])
    cls = ctx["classes"].get(canonical(v.get("class_title", "")))
    if v.get("class_title") and cls is None:
        errs.append(f"کلاس «{v['class_title']}» پیدا نشد")
    teacher = ctx["teachers"].get(v.get("teacher_national_id", ""))
    if v.get("teacher_national_id") and teacher is None:
        errs.append("معلمی با این کد ملی در سامانه نیست (ابتدا دبیران را وارد کنید)")
    elif teacher is not None and teacher.pk not in ctx["teacher_members"] and teacher.org_id != ctx["school"].pk:
        errs.append("این معلم عضو کادر همین مدرسه نیست")
    if cls is not None and v.get("title"):
        key = (canonical(v["title"]), cls.pk)
        if key in ctx["course_keys"]:
            errs.append("این درس برای این کلاس قبلاً ثبت شده است")
        errs += _dup(ctx, ("course", key), n)
    return errs


def split_list(value):
    """'دهم، یازدهم / دوازدهم' -> ['دهم', 'یازدهم', 'دوازدهم'] (Persian comma, Latin comma, slash, semicolon)."""
    import re
    return [p.strip() for p in re.split(r"[،,/;|]", value or "") if p.strip()]


def check_subjects(v, ctx, n):
    """Curriculum catalogue (سرفصل). A subject may list several grades and several field codes."""
    from apps.academics.models import Subject
    errs = _required(v, [("code", "کد درس"), ("title", "نام درس"), ("grade", "پایه")])
    if v.get("code"):
        if v["code"] in ctx["subject_codes"]:
            errs.append("کد درس قبلاً ثبت شده است")
        errs += _dup(ctx, ("subject", v["code"]), n)
    grades, fields = [], []
    for name in split_list(v.get("grade", "")):
        g = ctx["grades"].get(canonical(name))
        if g is None:
            errs.append(f"پایه «{name}» در کاتالوگ پیدا نشد")
        else:
            grades.append(g)
    for code in split_list(v.get("field_code", "")):
        f = ctx["fields_by_code"].get(code)
        if f is None:
            errs.append(f"رشته با کد {code} در کاتالوگ نیست")
        else:
            fields.append(f)
    if grades and not errs:
        problems = Subject(code=v.get("code", ""), name=v.get("title", "")).link_problems(grades, fields)
        errs += problems
    v["_grades"], v["_fields"] = grades, fields
    return errs


def create_subject(v, ctx):
    from apps.academics.models import Subject
    subj = Subject.objects.get_or_create(code=v["code"], defaults={"name": v["title"], "subject_type": v.get("subject_type", "")})[0]
    subj.grades.set(v.get("_grades") or [])
    subj.study_fields.set(v.get("_fields") or [])


def _course_parts(name):
    """'10081- زبان خارجي1' -> ('10081', 'زبان خارجی1');  'زبان خارجی1' -> ('', 'زبان خارجی1')"""
    import re as _re
    m = _re.match(r"^\s*(\d{3,8})\s*[-–]\s*(.+)$", name or "")
    return (m.group(1), m.group(2).strip()) if m else ("", (name or "").strip())


def _grade_for_row(school, v):
    """The grade named in the row, inside the school's level."""
    from apps.academics.models import Grade
    level = _school_level(school)
    if level is None or not v.get("grade"):
        return None
    return Grade.objects.filter(level=level, name=v["grade"]).first()


def _class_for_row(ctx, v):
    """The class of a teacher-list row: by its code (the group code) first, then by its title and grade."""
    code = (v.get("_class_code") or "").strip()
    if code and code in ctx["classes_by_code"]:
        return ctx["classes_by_code"][code]
    return _resolve_class(ctx, v.get("class_title", ""), v.get("grade", ""))


def _split_class(v):
    """«251-7724642» in the Seyda teacher list is the class title and the class code (group code)."""
    raw = (v.get("class_title") or "").strip()
    if "-" in raw:
        title, _, code = raw.partition("-")
        v["class_title"], v["_class_code"] = title.strip(), code.strip()
    v.setdefault("_class_code", (v.get("group_code") or "").strip())


def _teacher_of_school(school, personnel_code):
    """A teacher of this school found by personnel code (for a row without a national id)."""
    from apps.accounts.models import SchoolMembership
    if not personnel_code:
        return None
    m = SchoolMembership.objects.filter(school=school, user__personnel_code=personnel_code).select_related("user").first()
    return m.user if m else None


def check_class_teachers(v, ctx, n):
    """One row = a teacher teaching a course in a class (the Seyda class list)."""
    _split_class(v)
    known = _teacher_of_school(ctx["school"], v.get("personnel_code", "")) if not v.get("national_id") else None
    if known is not None:
        v["national_id"] = known.national_id or ""
        v.setdefault("first_name", known.first_name)
        v.setdefault("last_name", known.last_name)
    required = [("course_name", "نام درس"), ("class_title", "کلاس")]
    if not known:
        required = [("first_name", "نام معلم"), ("last_name", "نام خانوادگی معلم"), ("national_id", "کد ملی معلم")] + required
    errs = _required(v, required)
    nid = v.get("national_id", "")
    if nid and not is_valid_national_id(nid):
        errs.append("کد ملی معلم نامعتبر است (کنترل الگوریتم)")
    if v.get("class_title") and _class_for_row(ctx, v) is None:
        # a class missing from the school is added with its grade (the grade must exist in the school's level)
        if _grade_for_row(ctx["school"], v) is None:
            errs.append(f"پایه «{v.get('grade', '')}» در مقطع این مدرسه تعریف نشده است؛ کلاس «{v['class_title']}» ساخته نشد")
    code, title = _course_parts(v.get("course_name", ""))
    v["_subject_code"], v["_course_title"] = code, title
    if not title:
        errs.append("نام درس لازم است")
    cls = _class_for_row(ctx, v)
    if cls is not None and title:
        key = (canonical(title), cls.pk)
        if key in ctx["course_keys"]:
            errs.append("این درس برای این کلاس قبلاً ثبت شده است")
        errs += _dup(ctx, ("course", key), n)
    if nid and nid in ctx["db_nids"] and nid not in ctx["teachers"]:
        errs.append("این کد ملی متعلق به کاربر دیگری است (نه دبیر)")
    return errs


def create_class_teacher(v, ctx, school, year):
    from apps.accounts.models import SchoolMembership
    _split_class(v)
    nid = v.get("national_id", "")
    teacher = ctx["teachers"].get(nid) if nid else None
    if teacher is None:
        teacher = _teacher_of_school(school, v.get("personnel_code", ""))
    if teacher is None:
        teacher = User(username=nid, first_name=v["first_name"], last_name=v["last_name"], national_id=nid,
                       role=User.Role.TEACHER, org=school, father_name=v.get("father_name", ""))
        teacher.set_unusable_password()
        teacher.save()
        ctx["teachers"][nid] = teacher
    else:
        SchoolMembership.objects.get_or_create(user=teacher, school=school)
    cls = _class_for_row(ctx, v)
    if cls is None and v.get("class_title"):
        grade = _grade_for_row(school, v)
        if grade is not None:
            from apps.academics.models import ClassRoom
            cls = ClassRoom.objects.create(school=school, academic_year=year, name=v["class_title"][:100],
                                           code=(v.get("_class_code") or "")[:20], grade=grade)
            if cls.code:
                ctx["classes_by_code"][cls.code] = cls
    subject = ctx["subjects_by_code"].get(v.get("_subject_code", "")) if v.get("_subject_code") else None
    Course.objects.create(org=school, title=v["_course_title"], teacher=teacher, classroom=cls,
                          subject=subject, academic_year=year)



# ------------------------------------------------------------------ staff of the school (کادر مدرسه)
STAFF_PRECEDENCE = {"teacher": 1, "school_deputy_edu": 2, "school_deputy_exec": 2, "school_deputy_culture": 2, "school_admin": 3}


def staff_role(post):
    """The role a «پست» text stands for, or None when it is not a role of the school."""
    from apps.core.persian import normalize_text
    p = normalize_text(post or "")
    if "مدیر" in p and "معاون" not in p:
        return "school_admin"
    if "معاون" in p:
        if "اجرا" in p:
            return "school_deputy_exec"
        if "پرورش" in p:
            return "school_deputy_culture"
        if "آموزش" in p:
            return "school_deputy_edu"
    if "معلم" in p:
        return "teacher"
    return None


def check_staff(v, ctx, n):
    from apps.accounts.models import SchoolProfile, county_of
    errs = _required(v, [("personnel_code", "کد پرسنلی"), ("first_name", "نام"), ("last_name", "نام خانوادگی"),
                         ("national_id", "کد ملی"), ("post", "پست"), ("school_code", "کد مدرسه")])
    if v.get("status") and "فعال" not in v["status"]:
        errs.append("وضعیت این فرد فعال نیست؛ ثبت نمی‌شود")
    nid = v.get("national_id", "")
    if nid and not is_valid_national_id(nid):
        errs.append("کد ملی نامعتبر است (کنترل الگوریتم)")
    if v.get("post") and staff_role(v["post"]) is None:
        errs.append(f"پست «{v['post']}» شناخته نشد (معلم، مدیر، معاون آموزشی/اجرایی/پرورشی)")
    school = ctx["school"]
    profile = SchoolProfile.objects.filter(school=school).first()
    expected = (profile.school_code if profile and profile.school_code else school.code) or ""
    if v.get("school_code") and expected and v["school_code"] != expected:
        errs.append("کد مدرسه با مدرسه انتخاب‌شده یکی نیست")
    if nid:
        existing = User.objects.filter(national_id=nid).select_related("org").first()
        if existing is not None and existing.org_id and existing.org_id != school.pk:
            if county_of(existing.org) != county_of(school):
                errs.append("این کد ملی در مدرسه‌ای از شهرستان دیگر ثبت شده است")
    return errs


def create_staff(v, ctx, school):
    from apps.accounts.models import SchoolProfile
    role = staff_role(v["post"])
    profile = SchoolProfile.objects.filter(school=school).first()
    u = User.objects.filter(national_id=v["national_id"]).first()
    if u is None:
        u = User(username=v["national_id"], first_name=v["first_name"], last_name=v["last_name"],
                 national_id=v["national_id"], role=role, org=school, personnel_code=v.get("personnel_code", "")[:20])
        if role == "school_admin" and profile is not None:
            u.expert_level = profile.level
        u.set_unusable_password()          # the password is set by an administrator (reset, shown once)
        u.save()
    else:
        if not u.personnel_code and v.get("personnel_code"):
            u.personnel_code = v["personnel_code"][:20]
            u.save(update_fields=["personnel_code"])
    if u.pk and STAFF_PRECEDENCE[role] > STAFF_PRECEDENCE.get(u.role, 0):
        # the same person listed twice (for example as teacher and as deputy): the higher role is kept
        u.role = role
        if role == "school_admin" and profile is not None:
            u.expert_level = profile.level
        if u.org_id is None:
            u.org = school
        u.save()
    SchoolMembership.objects.get_or_create(user=u, school=school)



# ------------------------------------------------------------------ branches (شاخه) allowed for a school level
_KNOWN_BRANCHES = ["عمومی", "نظری", "کاردانش", "فنی و حرفه‌ای", "فنی حرفه‌ای و کاردانش", "حرفه‌ای استثنایی"]


def _is_secondary(level):
    from apps.core.persian import normalize_text
    n = normalize_text(level.name or "")
    return "متوسطه" in n and "دوم" in n


def _canonical_branch(level, name):
    """The branch name as the system spells it, or None when this level may not have it.
    «عمومی» is for every level; the other branches belong to متوسطه دوم."""
    from apps.core.persian import normalize_text
    want = normalize_text(name or "").strip()
    for known in _KNOWN_BRANCHES:
        if normalize_text(known) == want:
            if known == "عمومی" or _is_secondary(level):
                return known
            return None
    return None


# ------------------------------------------------------------------ fields (رشته) of a school
def _school_level(school):
    from apps.accounts.models import SchoolProfile
    profile = SchoolProfile.objects.filter(school=school).select_related("level").first()
    return profile.level if profile else None


def check_field(v, ctx, n):
    from apps.academics.models import Branch, SchoolField, StudyField
    errs = _required(v, [("field_code", "کد رشته"), ("field_name", "عنوان")])
    if errs:
        return errs
    school = ctx["school"]
    level = _school_level(school)
    branch = None
    if v.get("branch"):
        if level is None:
            errs.append("مقطع این مدرسه در مشخصات مدرسه تعیین نشده است")
        elif _canonical_branch(level, v["branch"]) is None:
            errs.append(f"شاخه «{v['branch']}» برای مقطع «{level.name}» مجاز نیست")
        else:
            branch = True
    existing = StudyField.objects.filter(code=v["field_code"]).first()
    if existing is not None and existing.name != v["field_name"]:
        errs.append("این کد رشته قبلاً با عنوان دیگری ثبت شده است")
    if existing is not None and branch is not None and existing.branch_id and existing.branch.name != _canonical_branch(level, v["branch"]):
        errs.append("این رشته قبلاً در شاخه‌ی دیگری ثبت شده است")
    if SchoolField.objects.filter(school=school, field=existing).exists() if existing else False:
        errs.append("این رشته از قبل برای این مدرسه ثبت شده است (تکراری)")
    return errs


def create_field(v, ctx, school):
    from apps.academics.models import Branch, SchoolField, StudyField
    level = _school_level(school)
    branch = None
    if v.get("branch"):
        canon = _canonical_branch(level, v["branch"])
        branch, _ = Branch.objects.get_or_create(level=level, name=canon)
    field = StudyField.objects.filter(code=v["field_code"]).first()
    if field is None:
        field = StudyField.objects.get_or_create(code=v["field_code"], defaults={"name": v["field_name"], "branch": branch, "level": level})[0]
    elif field.branch_id is None:
        field.branch = branch
        field.save(update_fields=["branch"])
    SchoolField.objects.get_or_create(school=school, field=field)



# ------------------------------------------------------------------ lessons of a grade (برنامه درسی)
def _base_level(text):
    """«متوسطه دوم - نظری» → «متوسطه دوم» (the branch after the dash is read separately)."""
    from apps.core.persian import normalize_text
    return normalize_text((text or "").split(" - ")[0]).strip()


def _branch_from_level_text(level, text):
    """The branch named after the dash in the level text, as the system spells it (None when there is none)."""
    if " - " not in (text or ""):
        return None
    return _canonical_branch(level, text.split(" - ", 1)[1].strip())


def _no_grade(name):
    from apps.core.persian import normalize_text
    return normalize_text(name or "") in ("", "نامشخص")


def check_curriculum(v, ctx, n):
    from apps.academics.models import Grade, SchoolField, StudyField, Subject
    errs = _required(v, [("subject_code", "کد درس"), ("subject_name", "نام درس")])
    if errs:
        return errs
    school = ctx["school"]
    level = _school_level(school)
    if level is None:
        return ["مقطع مدرسه در مشخصات مدرسه تعیین نشده است"]
    lt = v.get("level_text", "")
    if lt and _base_level(lt) != _base_level(level.name):
        errs.append("مقطع این ردیف با مقطع مدرسه یکی نیست")
    if lt and " - " in lt and _branch_from_level_text(level, lt) is None:
        errs.append(f"شاخه‌ی «{lt.split(' - ', 1)[1]}» برای مقطع این مدرسه مجاز نیست")
    grade = None
    if not _no_grade(v.get("grade")):
        grade = Grade.objects.filter(level=level, name=v["grade"]).first()
        if grade is None:
            errs.append(f"پایه «{v['grade']}» در این مقطع تعریف نشده است")
    field = StudyField.objects.filter(code=v["field_code"]).first() if v.get("field_code") else None
    if v.get("field_code") and field is None and not v.get("field_name"):
        errs.append("رشته با این کد در سامانه نیست و نام رشته‌ای در فایل نیست")
    sub = Subject.objects.filter(code=v["subject_code"]).first()
    sharing = "specialized" if v.get("field_code") else "general"
    if sub is not None:
        if sub.name != v["subject_name"]:
            errs.append("این کد درس قبلاً با نام دیگری ثبت شده است")
        # a lesson found in more than one grade is general; a lesson of a grade it is not yet defined in is added
        if field is not None and sub.study_fields.filter(pk=field.pk).exists() and (grade is None or sub.grades.filter(pk=grade.pk).exists()):
            errs.append("این درس برای این پایه و رشته از قبل ثبت است (تکراری)")
        if field is None and not v.get("field_code") and grade is not None and sub.grades.filter(pk=grade.pk).exists() and not sub.study_fields.exists():
            errs.append("این درس عمومی برای این پایه از قبل ثبت است (تکراری)")
    return errs


def create_curriculum(v, ctx, school):
    from apps.academics.models import Grade, SchoolField, StudyField, Subject
    level = _school_level(school)
    grade = None if _no_grade(v.get("grade")) else Grade.objects.filter(level=level, name=v["grade"]).first()
    field = None
    if v.get("field_code"):
        field = StudyField.objects.filter(code=v["field_code"]).first()
        if field is None:
            from apps.academics.models import Branch
            br = _branch_from_level_text(level, v.get("level_text", ""))
            branch = Branch.objects.get_or_create(level=level, name=br)[0] if br else None
            field = StudyField.objects.get_or_create(code=v["field_code"], defaults={"name": (v.get("field_name") or v["field_code"])[:150], "level": level, "branch": branch})[0]
        SchoolField.objects.get_or_create(school=school, field=field)
    sub, _ = Subject.objects.get_or_create(code=v["subject_code"], defaults={"name": v["subject_name"], "sharing": "general"})
    if v.get("selection") and not sub.subject_type:
        sub.subject_type = v["selection"][:40]
    if grade is not None:
        sub.grades.add(grade)
    if field is not None:
        sub.study_fields.add(field)
    # in more than one grade → general; otherwise it follows the row (with a field: specialized)
    if sub.grades.count() > 1:
        sub.sharing = "general"
    elif not sub.sharing:
        sub.sharing = "specialized" if field is not None else "general"
    sub.save()


def check_class_student(v, ctx, n):
    """One row = a student that goes into a class of this school (the Seyda «تخصیص دانش‌آموز» list)."""
    from apps.accounts.models import User
    errs = _required(v, [("student_code", "کد دانش‌آموز"), ("class_title", "کلاس")])
    if v.get("class_title"):
        classroom = _resolve_class(ctx, v.get("class_title", ""), v.get("grade", ""))
        if classroom is None:
            errs.append("کلاسی با این نام در سال جاری همین مدرسه نیست؛ ابتدا کلاس را وارد کنید.")
        else:
            v["_class_id"] = classroom.pk
            # the student's grade must be the grade of the class (پایه‌ی دانش‌آموز و پایه‌ی کلاس یکی باشد)
            want = canonical(v.get("grade", "")) if v.get("grade") else ""
            have = canonical(classroom.grade.name) if classroom.grade_id else ""
            if want and have and want != have:
                errs.append(f"پایه‌ی این ردیف «{v.get('grade')}» با پایه‌ی کلاس «{classroom.grade.name}» یکی نیست.")
    code = (v.get("student_code") or "").strip()
    user = User.objects.filter(role="student", student_code__in=[code, code.zfill(10)]).first() if code else None
    if code and user is None:
        errs.append("دانش‌آموزی با این کد در سامانه نیست؛ ابتدا ورود دانش‌آموزان را انجام دهید.")
    elif user is not None:
        v["_student_id"] = user.pk
    return errs


CHECKERS = {"class_students": check_class_student, "curriculum": check_curriculum, "fields": check_field, "staff": check_staff, "class_teachers": check_class_teachers, "students": check_students, "teachers": check_teachers, "classes": check_classes, "courses": check_courses,
            "subjects": check_subjects}


def validate(kind, school, rows):
    year = AcademicYear.objects.filter(is_current=True).first()
    if year is None:
        raise NoCurrentYear("سال تحصیلی جاری تعریف نشده است.")
    ctx = _context(kind, school, year, rows)
    out = []
    for r in rows:
        v = {k: ("" if val is None else str(val)).strip() for k, val in r["values"].items()}
        errs = CHECKERS[kind](v, ctx, r["n"])
        out.append({"n": r["n"], "values": v, "errors": errs, "status": "error" if errs else "ok"})
    return out


# ------------------------------------------------------------------ writing (only rows that passed validate)
def _ensure_student_field(v, school, ctx):
    """A student's field (رشته) that the catalogue lacks is added once (by code), and linked to the school once."""
    from apps.academics.models import SchoolField, StudyField
    code = (v.get("field_code") or "").strip()
    if not code:
        return
    field = ctx["fields_by_code"].get(code) or StudyField.objects.filter(code=code).first()
    if field is None:
        field = StudyField.objects.get_or_create(code=code, defaults={"name": (v.get("field") or code)[:150], "level": _school_level(school)})[0]
    ctx["fields_by_code"][code] = field
    SchoolField.objects.get_or_create(school=school, field=field)


def _create(kind, school, year, ctx, v):
    if kind == "class_students":
        if not v.get("_class_id") or not v.get("_student_id"):
            raise ValueError("این ردیف به کلاس یا دانش‌آموز در سامانه وصل نشده است؛ کد دانش‌آموز و نام کلاس را بررسی کنید.")
        ClassRoom.objects.get(pk=v["_class_id"]).students.add(v["_student_id"])
        return
    if kind == "classes":
        _ensure_student_field(v, school, ctx)
        ClassRoom.objects.create(school=school, academic_year=year, name=v["title"], code=v.get("code", ""),
                                 capacity=int(v["capacity"]) if v.get("capacity") else None,
                                 grade=ctx["grades"][canonical(v["grade"])],
                                 study_field=ctx["fields_by_code"].get(v.get("field_code", "")) or
                                 ctx["fields"].get(canonical(v.get("field", ""))))
    elif kind == "teachers":
        u = User(username=v["national_id"], first_name=v["first_name"], last_name=v["last_name"],
                 national_id=v["national_id"], mobile=v.get("mobile", ""), role=User.Role.TEACHER, org=school)
        u.set_unusable_password()
        u.save()
    elif kind == "students":
        _ensure_student_field(v, school, ctx)
        nid_value = v.get("national_id") or (v.get("student_code") if is_valid_national_id(v.get("student_code") or "") else "")
        username = nid_value or v.get("student_code")
        u = User(username=username, first_name=v["first_name"], last_name=v["last_name"], role=User.Role.STUDENT,
                 org=school, student_code=v.get("student_code") or None, national_id=nid_value or None,
                 father_name=v.get("father_name", ""), gender=v.get("gender", ""))
        u.set_unusable_password()          # log-in is enabled by an admin password reset (shown once)
        u.save()
        from apps.transfers.models import open_history
        open_history(u, school, reason="ورود دسته‌ای")
        # the class is set later: when the row names a class that exists, the student joins it now
        cls = _resolve_class(ctx, v["class_title"], v.get("grade", "")) if v.get("class_title") else None
        if cls is not None:
            cls.students.add(u)
        for course in (Course.objects.filter(classroom=cls) if cls is not None else []):
            Enrollment.objects.get_or_create(course=course, student=u,
                                             defaults={"academic_year_id": course.academic_year_id})
    elif kind == "subjects":
        create_subject(v, ctx)
    elif kind == "class_teachers":
        create_class_teacher(v, ctx, school, year)
    elif kind == "staff":
        create_staff(v, ctx, school)
    elif kind == "fields":
        create_field(v, ctx, school)
    elif kind == "curriculum":
        create_curriculum(v, ctx, school)
    elif kind == "courses":
        cls = ctx["classes"][canonical(v["class_title"])]
        Course.objects.create(org=school, title=v["title"], teacher=ctx["teachers"][v["teacher_national_id"]],
                              classroom=cls, academic_year=year)


def commit(kind, school, rows):
    """rows: validated rows. Writes the ok ones, each in its own savepoint. Returns (created, failed)."""
    year = AcademicYear.objects.filter(is_current=True).first()
    ctx = _context(kind, school, year, rows)
    created, failed = [], []
    for r in rows:
        if r["status"] != "ok":
            continue
        try:
            with transaction.atomic():
                _create(kind, school, year, ctx, r["values"])
            created.append(r["n"])
        except IntegrityError:
            failed.append({"n": r["n"], "reason": "خطای یکتایی در پایگاه داده (احتمالاً تکراری)"})
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the whole import
            failed.append({"n": r["n"], "reason": f"خطا در ثبت این ردیف: {str(exc)[:200]}"})
    return created, failed


def missing_classes(rows):
    """Distinct classes the student rows need but the school does not have: [{grade, code, field_code}]."""
    out, seen = [], set()
    for r in rows:
        key = (r.get("values") or {}).get("_missing_class") or ""
        if key and key not in seen:
            seen.add(key)
            grade, code, field_code = (key.split("|") + ["", "", ""])[:3]
            out.append({"grade": grade, "code": code, "field_code": field_code})
    return out


def create_missing_classes(school, year, missing):
    """Creates the classes the student list needs (grade + class number + field). Returns (created, skipped reasons)."""
    grades = {canonical(g.name): g for g in _grades_for_school(school)}
    fields = {f.code: f for f in StudyField.objects.exclude(code=None)}
    created, skipped = 0, []
    for m in missing:
        g = grades.get(canonical(m["grade"]))
        if g is None:
            skipped.append(f"پایه «{m['grade']}» در مقطع مدرسه پیدا نشد")
            continue
        name = f"{g.name} {m['code']}"
        if ClassRoom.objects.filter(school=school, academic_year=year, name=name).exists():
            skipped.append(f"کلاس «{name}» از قبل وجود دارد")
            continue
        field = fields.get(m["field_code"]) if m["field_code"] else None
        if field is not None and field.level_id not in (None, g.level_id):
            field = None
        ClassRoom.objects.create(school=school, academic_year=year, name=name, code=m["code"], grade=g, study_field=field)
        created += 1
    return created, skipped
