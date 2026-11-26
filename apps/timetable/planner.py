"""برنامه‌ریزی درسی: ورودی از پایگاه داده، ساخت پیش‌نویس، بررسی تداخل، تأیید و ویرایش امن.

قواعد (همه‌ی آن‌ها در ساخت و در ویرایش دستی کنترل می‌شوند):
  - هر کلاس در هر زنگ فقط یک درس؛ هر دبیر در هر زنگ فقط در یک کلاس.
  - دبیر فقط در ساعت‌های حضورش؛ هر درس در یک روز حداکثر یک بار.
  - درس کارگاهی فقط در کارگاهی که به رشته‌ی کلاس وصل است و ظرفیت آن در آن زنگ پر نشده.
  - دروس سخت هر کلاس (انتخاب مدیر/معاون پیش از برنامه‌ریزی) سختی ۵ می‌گیرند.
  - تنها یک نسخه‌ی تأییدشده برای هر مدرسه؛ تأیید نسخه‌ی جدید، نسخه‌ی قبلی را بایگانی می‌کند.
"""
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from apps.academics.models import ClassRoom, GradeWeeklyHours, Subject
from apps.courses.models import Course
from apps.timetable.models import (BellPeriod, ClassHardLesson, TeacherAvailability, TimetableEntry,
                                   TimetableVersion, Workshop)
from apps.timetable.solver import check_grid, solve

DAYS = [0, 1, 2, 3, 4]          # شنبه تا چهارشنبه (۵ روز مدرسه)
PALETTE = ["#0f5c4d", "#2563eb", "#b45309", "#7c3aed", "#be123c", "#0e7490", "#4d7c0f", "#c2410c",
           "#1d4ed8", "#a16207", "#6d28d9", "#15803d", "#9d174d", "#334155", "#0369a1", "#854d0e"]


def colour_of(subject_id):
    return PALETTE[(subject_id or 0) % len(PALETTE)]


def _periods(school):
    return list(BellPeriod.objects.filter(school=school).order_by("number").values_list("number", flat=True))


def _teacher_free(school, teacher_ids, all_slots=None):
    """زنگ‌های آزاد هر دبیر. دبیری که هیچ ساعت حضوری تعریف نکرده، در همه‌ی زنگ‌ها آزاد است
    (سامانه روز و زنگ را خودش تعیین می‌کند)؛ دبیری که ساعت حضور دارد فقط در همان زنگ‌ها."""
    free = defaultdict(set)
    for ta in TeacherAvailability.objects.filter(teacher_id__in=teacher_ids, period__school=school).select_related("period"):
        free[ta.teacher_id].add((ta.weekday, ta.period.number))
    if all_slots is not None:
        for t in teacher_ids:
            if t not in free:
                free[t] = set(all_slots)
        # the teacher's lessons in other schools: a bell here that overlaps one of them in time is not free
        blocked = _external_blocks(school, teacher_ids, all_slots)
        for t, slots in blocked.items():
            if t in free:
                free[t] = free[t] - slots
    return free


EXTERNAL_HOURS = {}


def _external_blocks(school, teacher_ids, all_slots):
    """زنگ‌های این مدرسه که با درس دبیر در مدرسه‌های دیگر (نسخه‌ی تأییدشده) از نظر ساعت همپوشانی دارند."""
    from datetime import datetime as _dt
    def _mins(t):
        return t.hour * 60 + t.minute
    other = TimetableEntry.objects.filter(version__status=TimetableVersion.APPROVED, teacher_id__in=teacher_ids) \
        .exclude(version__school=school).select_related("period", "version__school")
    busy = defaultdict(list)  # (teacher, weekday) -> [(start, end)]
    for e in other:
        busy[(e.teacher_id, e.weekday)].append((_mins(e.period.start_time), _mins(e.period.end_time)))
        EXTERNAL_HOURS[(e.teacher_id, e.weekday)] = EXTERNAL_HOURS.get((e.teacher_id, e.weekday), 0) + (e.piece_hours or 1)
    mine = {b.number: (_mins(b.start_time), _mins(b.end_time)) for b in BellPeriod.objects.filter(school=school)}
    out = defaultdict(set)
    for (t, wd), intervals in busy.items():
        for (d, p) in all_slots:
            if d != wd or p not in mine:
                continue
            a, b = mine[p]
            if any(a < y and x < b for (x, y) in intervals):
                out[t].add((d, p))
    return out


TECHNICAL_LEVELS = {"FNI", "KAR"}  # هنرستان فنی و کاردانش


def _is_technical_school(school):
    """مدرسه‌ی هنرستان یا کاردانش است؛ با کد مقطع یا با نام مقطع/مدرسه (در صورت متفاوت بودن کد)."""
    from apps.accounts.models import SchoolProfile
    prof = SchoolProfile.objects.filter(school=school).select_related("level").first()
    if prof is None or prof.level is None:
        return False
    if prof.level.code in TECHNICAL_LEVELS:
        return True
    text = f"{prof.level.name} {school.name}"
    return ("هنرستان" in text) or ("کاردانش" in text)


def _block_sizes(hours):
    """تقسیم ساعت درس به بلوک‌ها: ۸ = ۴+۴، ۶ = ۴+۲، ۷ = ۴+۲+۱، ۵ = ۴+۱ (هر بلوک در یک روز)."""
    h = int(hours)
    if h <= 4:
        return [h]
    out = [4] * (h // 4)
    rem = h % 4
    if rem == 3:
        out += [2, 1]
    elif rem:
        out.append(rem)
    return out


def _split_technical_blocks(lessons):
    """درس‌های بیش از ۴ ساعت به بلوک تقسیم می‌شوند؛ هر بلوک یک «درس» مستقل با شناسه‌ی یکتا برای موتور است،
    و به همان درس اصلی (sid) در برنامه ذخیره می‌شود. بلوک‌ها سعی می‌کنند هر کدام در یک روز بیایند."""
    out = []
    for l in lessons:
        sizes = _block_sizes(l["hours"])
        if len(sizes) == 1:
            out.append(l)
            continue
        for k, size in enumerate(sizes):
            b = dict(l)
            b["id"] = f"{l['id']}#{k}"
            b["sid"] = l["id"]
            b["hours"] = size
            b["max_per_day"] = size
            out.append(b)
    return out


def _lessons_of(cls):
    hard = set(ClassHardLesson.objects.filter(classroom=cls).values_list("subject_id", flat=True))
    out = []
    for c in Course.objects.filter(classroom=cls, subject__isnull=False).select_related("subject", "teacher"):
        sub = c.subject
        gw = GradeWeeklyHours.objects.filter(grade=cls.grade, subject=sub, field=cls.study_field).first() \
            or GradeWeeklyHours.objects.filter(grade=cls.grade, subject=sub, field__isnull=True).first()
        hours = gw.hours if gw else (sub.weekly_hours or 1)
        out.append({"id": sub.pk, "name": sub.name, "teacher": c.teacher_id, "hours": hours,
                    "difficulty": 5 if sub.pk in hard else sub.difficulty,
                    "max_per_day": max(1, int(sub.max_per_day or 1)), "workshop": sub.kind == "workshop", "field": cls.study_field_id, "course": c.pk})
    return out


def _workshop_pool(school):
    return list(Workshop.objects.filter(school=school).prefetch_related("fields"))


def _pick_workshop(pool, field_id, slot_use, slot):
    """کارگاهی که به رشته‌ی کلاس وصل است و در این زنگ ظرفیت دارد."""
    for w in pool:
        if field_id in {f.pk for f in w.fields.all()} and slot_use[(w.pk, slot)] < w.capacity:
            return w
    return None


@transaction.atomic
def generate(school, user, title="", thursday=False):
    classes = list(ClassRoom.objects.filter(school=school, academic_year__is_current=True)
                   .select_related("grade", "study_field"))
    periods = _periods(school)
    if not classes or not periods:
        return {"ok": False, "errors": ["کلاس‌های سال جاری یا زنگ‌های مدرسه کافی نیستند."]}
    inputs = []
    teacher_ids = set()
    technical = _is_technical_school(school)
    for cls in classes:
        lessons = _lessons_of(cls)
        if technical:
            lessons = _split_technical_blocks(lessons)
        teacher_ids.update(l["teacher"] for l in lessons if l["teacher"])
        inputs.append({"id": cls.pk, "name": cls.name, "lessons": lessons})
    pool = _workshop_pool(school)
    rooms = sum(w.capacity for w in pool)
    from apps.timetable.models import BellPeriod as _BP
    period_hours = {b.number: b.hours for b in _BP.objects.filter(school=school)}
    day_list = DAYS + ([5] if thursday else [])
    weighted = any(h > 1 for h in period_hours.values())
    if weighted:
        # schools with bells longer than one hour: the weighted engine (a bell gives its own hours)
        if any(l["workshop"] for c in inputs for l in c["lessons"]):
            return {"ok": False, "errors": ["در مدرسه‌هایی با زنگ بیش از یک ساعته، درس کارگاهی هنوز پشتیبانی نمی‌شود؛ ابتدا آن را جدا کنید."]}
        from apps.timetable.solver_weighted import solve_weighted
        from apps.accounts.models import User as _U
        tids = {l["teacher"] for c in inputs for l in c["lessons"] if l.get("teacher")}
        names = {u.pk: (u.get_full_name() or u.username) for u in _U.objects.filter(pk__in=tids)}
        for c in inputs:
            for l in c["lessons"]:
                l["teacher_name"] = names.get(l.get("teacher"), "")
        EXTERNAL_HOURS.clear()
        _tf = _teacher_free(school, teacher_ids, [(d, p) for d in day_list for p in periods])
        result = solve_weighted(inputs, day_list, periods, period_hours, teacher_free=_tf, external_hours=dict(EXTERNAL_HOURS))
        if not result["ok"]:
            return {"ok": False, "errors": result["errors"]}
    else:
        # درس بیش از ۴ ساعت: اول تلاش می‌شود در یک روز جا شود؛ اگر نشد، به دو بخش ۴ ساعته (دو روز) شکسته می‌شود
        tf = _teacher_free(school, teacher_ids, [(d, p) for d in day_list for p in periods])
        def _attempt(single_day):
            for c in inputs:
                for l in c["lessons"]:
                    if l["hours"] > 4:
                        l["max_per_day"] = l["hours"] if single_day else 4
            return solve(inputs, day_list, periods, teacher_free=tf, workshop_rooms=rooms, period_hours=period_hours)
        result = _attempt(True)
        if not result["ok"] and any(l["hours"] > 4 for c in inputs for l in c["lessons"]):
            result = _attempt(False)
        if not result["ok"]:
            return {"ok": False, "errors": result["errors"]}

    version = TimetableVersion.objects.create(school=school, title=title or timezone.now().strftime("%Y-%m-%d %H:%M"),
                                              status=TimetableVersion.DRAFT, created_by=user)
    slot_use = defaultdict(int)
    entries, errors = [], []
    cls_by_id = {c.pk: c for c in classes}
    course_of = {(c["id"], l["id"]): l for c in inputs for l in c["lessons"]}
    if weighted:
        # each bell may hold one piece (part 0) or two one-hour pieces of two lessons (parts 0 and 1)
        bells = {b.number: b for b in BellPeriod.objects.filter(school=school)}
        extra_set = {(x["class"], x["day"], x["period"], x["lesson"]) for x in result.get("extra_days", [])}
        for cid, per_slot in result["shared"].items():
            for (day, per), pieces in per_slot.items():
                for part, (lid, size) in enumerate(pieces[:2]):
                    lesson = course_of[(cid, lid)]
                    is_extra = (cid, day, per, lid) in extra_set
                    entries.append(TimetableEntry(version=version, classroom_id=cid, weekday=day,
                                                  period=bells[per], subject_id=lesson.get("sid", lesson["id"]), teacher_id=lesson["teacher"],
                                                  workshop=None, part=part, piece_hours=int(size), extra_day=is_extra))
        unassigned = [{"class": cls_by_id[u["class"]].name, "day": u["day"], "period": u["period"], "hours": u["hours"]}
                      for u in result.get("unassigned", []) if u["class"] in cls_by_id]
        TimetableEntry.objects.bulk_create(entries)
        from .models import log_change
        log_change(version, user, "generate", f"ساخت برنامه: {len(entries)} جلسه، {len(unassigned)} ساعت خالی", before=[])
        return {"ok": True, "version": version.pk, "entries": len(entries), "errors": [], "unassigned": unassigned,
                "extra_days": [{"class": cls_by_id[x["class"]].name, "lesson": next((l["name"] for l in [course_of.get((x["class"], x["lesson"]))] if l), str(x["lesson"])), "day": x["day"], "period": x["period"]}
                               for x in result.get("extra_days", []) if x["class"] in cls_by_id]}
    for cid, cells in result["grid"].items():
        for (day, per), sid in cells.items():
            lesson = course_of[(cid, sid)]
            ws = None
            if lesson["workshop"]:
                slot = (day, per)
                ws = _pick_workshop(pool, lesson["field"], slot_use, slot)
                if ws is None:
                    errors.append(f"کارگاهی برای درس «{lesson['name']}» کلاس «{cls_by_id[cid].name}» در روز {day} زنگ {per} پیدا نشد.")
                    continue
                slot_use[(ws.pk, slot)] += 1
            entries.append(TimetableEntry(version=version, classroom_id=cid, weekday=day,
                                          period=BellPeriod.objects.get(school=school, number=per),
                                          subject_id=lesson.get("sid", lesson["id"]), teacher_id=lesson["teacher"], workshop=ws))
    TimetableEntry.objects.bulk_create(entries)
    if errors:
        version.delete()
        return {"ok": False, "errors": errors}
    return {"ok": True, "version": version.pk, "entries": len(entries), "errors": [], "unassigned": []}


def _technical_day_problems(version):
    """در هنرستان/کاردانش: ساعت یک درس در یک روز نباید از سقف بلوک بیشتر شود (درس بیش از ۴ ساعت: ۴ ساعت در روز)."""
    total = defaultdict(int)
    per_day = defaultdict(int)
    names = {}
    for e in version.entries.select_related("classroom", "subject"):
        h = e.piece_hours or 1
        total[(e.classroom_id, e.subject_id)] += h
        per_day[(e.classroom_id, e.subject_id, e.weekday)] += h
        names[(e.classroom_id, e.subject_id)] = (e.classroom.name, e.subject.name if e.subject_id else "")
    out = []
    for (cid, sid, wd), h in per_day.items():
        whole = total[(cid, sid)]
        limit = 4 if whole > 4 else whole
        if h > limit:
            cname, lname = names[(cid, sid)]
            out.append(f"درس «{lname}» در کلاس {cname} در روز {wd}: {h} ساعت است؛ سقف این درس در روز {limit} ساعت است.")
    return out


def version_problems(version):
    """همه‌ی تداخل‌های یک نسخه (برای بررسی پیش از تأیید)."""
    classes = {}
    for e in version.entries.select_related("classroom__grade", "subject", "teacher"):
        classes.setdefault(e.classroom_id, {"id": e.classroom_id, "name": e.classroom.name, "lessons": {}})
        classes[e.classroom_id]["lessons"].setdefault(e.subject_id, {"id": e.subject_id, "name": e.subject.name,
                                                                     "teacher": e.teacher_id, "hours": 0, "difficulty": 3})
    grid = defaultdict(dict)
    rooms = defaultdict(int)
    for e in version.entries.all():
        grid[e.classroom_id][(e.weekday, e.period.number)] = e.subject_id
        if e.workshop_id:
            rooms[(e.weekday, e.period.number)] += 1
    # check_grid expects each class's lessons as a list; here they are kept in a dict by subject id
    technical = _is_technical_school(version.school)
    class_list = [{"id": c["id"], "name": c["name"], "lessons": list(c["lessons"].values())} for c in classes.values()]
    if technical:
        # هنرستان و کاردانش: سقف روزانه بر اساس ساعت (بلوک‌ها)، نه تعداد جلسه؛ بررسی جدا پایین
        for c in class_list:
            for l in c["lessons"]:
                l["max_per_day"] = 99
    problems = check_grid(class_list, dict(grid))
    if technical:
        problems += _technical_day_problems(version)
    cap = sum(w.capacity for w in Workshop.objects.filter(school=version.school))
    for slot, n in rooms.items():
        if n > cap:
            problems.append(f"تداخل کارگاه در روز {slot[0]} زنگ {slot[1]}: {n} کلاس.")
    return problems


def edit_entry(version, data):
    """افزودن یا تغییر یک خانه با کنترل تداخل. اگر تداخلی باشد، چیزی ذخیره نمی‌شود."""
    if version.status not in (TimetableVersion.DRAFT, TimetableVersion.REVIEW):
        return False, ["فقط نسخه‌ی پیش‌نویس یا در انتظار بررسی قابل ویرایش است."], None
    school = version.school
    cls = ClassRoom.objects.select_related("grade", "study_field").get(pk=data["classroom"], school=school)
    period = BellPeriod.objects.get(school=school, number=int(data["period"]))
    weekday = int(data["weekday"])
    subject = Subject.objects.get(pk=data["subject"])
    teacher_id = data.get("teacher") or None
    ws = Workshop.objects.get(pk=data["workshop"], school=school) if data.get("workshop") else None
    errs = []
    others = version.entries.exclude(pk=data.get("entry_id") or 0)
    if others.filter(classroom=cls, weekday=weekday, period=period).exists():
        errs.append("این کلاس در این زنگ درس دیگری دارد.")
    if teacher_id and others.filter(teacher_id=teacher_id, weekday=weekday, period=period).exists():
        errs.append("این دبیر در این زنگ در کلاس دیگری تدریس می‌کند.")
    if teacher_id and TeacherAvailability.objects.filter(teacher_id=teacher_id, period__school=school).exists() \
            and not TeacherAvailability.objects.filter(teacher_id=teacher_id, weekday=weekday, period=period).exists():
        errs.append("دبیر در این روز و زنگ حضور ندارد.")
    if others.filter(classroom=cls, subject=subject, weekday=weekday).exists():
        errs.append("این درس امروز یک‌بار در این کلاس است (هر درس در روز حداکثر یک بار).")
    if ws is not None:
        if cls.study_field_id and not ws.fields.filter(pk=cls.study_field_id).exists():
            errs.append(f"کارگاه «{ws.name}» به رشته‌ی این کلاس وصل نیست.")
        if others.filter(workshop=ws, weekday=weekday, period=period).count() >= ws.capacity:
            errs.append(f"ظرفیت کارگاه «{ws.name}» در این زنگ پر است.")
    if errs:
        return False, errs, None
    entry = None
    if data.get("entry_id"):
        entry = version.entries.get(pk=data["entry_id"])
    else:
        entry = TimetableEntry(version=version)
    entry.classroom, entry.weekday, entry.period = cls, weekday, period
    entry.subject, entry.teacher_id, entry.workshop = subject, teacher_id, ws
    entry.save()
    return True, [], entry


@transaction.atomic
def submit(version):
    if version.status != TimetableVersion.DRAFT:
        return False, ["فقط پیش‌نویس را می‌توان برای بررسی فرستاد."]
    problems = version_problems(version)
    if problems:
        return False, problems
    version.status = TimetableVersion.REVIEW
    version.save(update_fields=["status"])
    return True, []


@transaction.atomic
def approve(version, user):
    if version.status != TimetableVersion.REVIEW:
        return False, ["فقط نسخه‌ی در انتظار بررسی را می‌توان تأیید کرد."]
    problems = version_problems(version)
    if problems:
        return False, problems
    TimetableVersion.objects.filter(school=version.school, status=TimetableVersion.APPROVED).update(status=TimetableVersion.ARCHIVED)
    version.status = TimetableVersion.APPROVED
    version.approved_by = user
    version.approved_at = timezone.now()
    version.save(update_fields=["status", "approved_by", "approved_at"])
    return True, []


def reject(version, note):
    if version.status != TimetableVersion.REVIEW:
        return False, ["فقط نسخه‌ی در انتظار بررسی را می‌توان برگرداند."]
    version.status = TimetableVersion.DRAFT
    version.note = (note or "")[:2000]
    version.save(update_fields=["status", "note"])
    return True, []


def regenerate(version, user):
    """تولید دوباره با همان محدودیت‌ها؛ نسخه‌ی قبلی بایگانی می‌شود و تأییدشده‌ها دست‌نخورده می‌مانند."""
    res = generate(version.school, user, title=f"{version.title} (بازتولید)")
    if res.get("ok"):
        TimetableVersion.objects.filter(pk=version.pk, status__in=[TimetableVersion.DRAFT, TimetableVersion.REVIEW]) \
            .update(status=TimetableVersion.ARCHIVED)
    return res


def retry_missing(version, school):
    """تلاش دوباره برای دروس جانگرفته‌ی یک پیش‌نویس: جلسه‌های موجود سر جای خود می‌مانند؛
    موتور وزنی فقط ساعت‌های باقی‌مانده را در زنگ‌های آزاد (با رعایت دبیران و سقف روزها) جا می‌دهد."""
    from apps.timetable.models import BellPeriod as _BP
    from apps.timetable.solver_weighted import solve_weighted
    from apps.accounts.models import User as _U
    classes = list(ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field"))
    periods = _periods(school)
    period_hours = {b.number: b.hours for b in _BP.objects.filter(school=school)}
    if not any(h > 1 for h in period_hours.values()):
        return {"ok": False, "errors": ["این تلاش دوباره فقط برای مدرسه‌هایی با زنگ بیش از یک ساعته است."]}
    inputs, teacher_ids = [], set()
    for cls in classes:
        lessons = _lessons_of(cls)
        teacher_ids.update(l["teacher"] for l in lessons if l["teacher"])
        inputs.append({"id": cls.pk, "name": cls.name, "lessons": lessons})
    tids = teacher_ids
    names = {u.pk: (u.get_full_name() or u.username) for u in _U.objects.filter(pk__in=tids)}
    for c in inputs:
        for l in c["lessons"]:
            l["teacher_name"] = names.get(l.get("teacher"), "")
    existing = list(TimetableEntry.objects.filter(version=version).select_related("classroom", "period", "subject", "teacher"))
    bells = {b.number: b for b in BellPeriod.objects.filter(school=school)}
    initial = []
    for e in existing:
        c = next((c for c in inputs if c["id"] == e.classroom_id), None)
        if c is None:
            continue
        li = next((i for i, l in enumerate(c["lessons"]) if l["id"] == e.subject_id), None)
        if li is None:
            continue
        size = e.piece_hours or bells[e.period.number].hours
        initial.append({"class": e.classroom_id, "li": li, "lesson": c["lessons"][li], "day": e.weekday,
                        "period": e.period.number, "size": size})
    days_present = {e.weekday for e in existing}
    day_list = DAYS + ([5] if 5 in days_present else [])
    EXTERNAL_HOURS.clear()
    _tf = _teacher_free(school, tids, [(d, p) for d in day_list for p in periods])
    result = solve_weighted(inputs, day_list, periods, period_hours, teacher_free=_tf,
                            initial=initial, external_hours=dict(EXTERNAL_HOURS))
    course_of = {(c["id"], l["id"]): l for c in inputs for l in c["lessons"]}
    used_parts = defaultdict(set)
    for e in existing:
        used_parts[(e.classroom_id, e.weekday, e.period_id)].add(e.part)
    new_entries = []
    for pc in result.get("new_pieces", []):
        cid, day, per, lid, size = pc["class"], pc["day"], pc["period"], pc["lesson"], pc["size"]
        lesson = course_of[(cid, lid)]
        bell = bells[per]
        free = [p for p in (0, 1) if p not in used_parts[(cid, day, bell.pk)]]
        if not free:
            continue
        part = free[0]
        used_parts[(cid, day, bell.pk)].add(part)
        new_entries.append(TimetableEntry(version=version, classroom_id=cid, weekday=day, period=bell,
                                          subject_id=lesson.get("sid", lesson["id"]), teacher_id=lesson["teacher"], workshop=None,
                                          part=part, piece_hours=int(size)))
    TimetableEntry.objects.bulk_create(new_entries)
    still = [s for s in result.get("lesson_status", []) if s["missing"] > 0]
    return {"ok": True, "added": len(new_entries), "errors": result.get("errors", []), "still_missing": len(still)}
