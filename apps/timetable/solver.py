"""موتور برنامه‌ریزی درسی (هسته‌ی حل، بدون پایگاه داده).

ورودی‌ها:
  classes      : فهرست کلاس‌ها؛ هر کلاس {"id", "name", "lessons": [درس‌ها]}
                 هر درس: {"id", "name", "teacher", "hours", "difficulty" (easy|medium|hard), "workshop" (bool)}
  days         : فهرست شماره‌ی روزهای هفته (مثلاً [0,1,2,3,4])
  periods      : فهرست شماره‌ی زنگ‌ها (مثلاً [1,2,...,8])
  teacher_free : {teacher_id: set((روز, زنگ))}  ساعت‌های حضور دبیر؛ None یعنی آزاد
  workshop_rooms : تعداد کارگاه‌های هم‌زمان در هر (روز، زنگ) (پیش‌فرض ۰)

قواعد (همه‌ی محدودیت‌های سخت):
  1. هر کلاس در هر (روز، زنگ) فقط یک درس دارد.
  2. هر دبیر در هر (روز، زنگ) فقط در یک کلاس تدریس می‌کند.
  3. دبیر فقط در ساعت‌های حضورش تدریس می‌کند.
  4. هر درس به تعداد ساعت هفتگی‌اش برنامه می‌شود.
  5. هر درس در یک روز حداکثر یک بار (یک «جلسه» در روز).
  6. درس کارگاهی فقط وقتی جای کارگاه خالی است قرار می‌گیرد (شمارش کارگاه‌ها).
قاعده‌ی نرم: پخش درس‌های سخت در روزهای مختلف (امتیاز بهتر برای روزهای کم‌باری).
خروجی: {"ok": bool, "grid": {class_id: {(روز, زنگ): درس‌id}}, "errors": [...] , "stats": {...}}
"""
from collections import defaultdict

TEXT_LEVEL = {"easy": 1, "medium": 3, "hard": 5}


def level_of(l):
    """سختی درس به عدد ۱ تا ۵ (متن قدیمی هم پذیرفته می‌شود)."""
    v = l.get("difficulty", 3)
    if isinstance(v, str):
        return TEXT_LEVEL.get(v, 3)
    return max(1, min(5, int(v or 3)))


def is_hard(l):
    return level_of(l) >= 4


def _precheck(classes, days, periods, teacher_free, workshop_rooms, period_hours=None):
    """بررسی‌های ساده‌ی امکان‌پذیری؛ علت ناممکن بودن را روشن می‌کند."""
    errors = []
    slots = [(d, p) for d in days for p in periods]
    # the bells' weight: a bell of 2 hours gives 2 hours of teaching (default 1 hour per bell)
    weight = {p: (period_hours or {}).get(p, 1) for p in periods}
    week_hours = sum(weight[p] for d in days for p in periods)
    for c in classes:
        need = sum(l["hours"] for l in c["lessons"])
        if need > week_hours:
            errors.append(f"کلاس «{c['name']}»: {need} ساعت لازم است ولی فقط {week_hours} ساعت در هفته از زنگ‌ها هست.")
        for l in c["lessons"]:
            if l["hours"] > len(days):
                errors.append(f"درس «{l['name']}» ({c['name']}): {l['hours']} ساعت در هفته، ولی فقط {len(days)} روز هست؛ هر درس حداکثر یک بار در روز است.")
            if teacher_free is not None and l.get("teacher") is not None:
                avail = teacher_free.get(l["teacher"], set())
                if len(avail & set(slots)) < l["hours"]:
                    errors.append(f"دبیر درس «{l['name']}» ({c['name']}): ساعت حضور کافی ندارد (لازم {l['hours']}، موجود {len(avail & set(slots))}).")
            if l.get("workshop") and workshop_rooms <= 0:
                errors.append(f"درس کارگاهی «{l['name']}» ({c['name']}): هیچ کارگاهی ثبت نشده است.")
    return errors


def solve(classes, days, periods, teacher_free=None, workshop_rooms=0, max_steps=200000, period_hours=None):
    errors = _precheck(classes, days, periods, teacher_free, workshop_rooms, period_hours)
    if errors:
        return {"ok": False, "grid": {}, "errors": errors, "stats": {}}

    slots = [(d, p) for d in days for p in periods]
    # هر «جلسه» یک (کلاس، درس، شماره‌ی جلسه) است؛ درس‌های سخت‌تر و محدودتر اول
    tasks = []
    for c in classes:
        for l in c["lessons"]:
            for k in range(l["hours"]):
                tasks.append((c["id"], l, k))
    def difficulty_key(t):
        cid, l, _ = t
        avail = len(teacher_free.get(l.get("teacher"), set())) if teacher_free is not None else len(slots)
        return (avail, -level_of(l), -int(bool(l.get("workshop"))))
    tasks.sort(key=difficulty_key)

    class_busy = defaultdict(set)      # class_id -> {slot}
    teacher_busy = defaultdict(set)    # teacher -> {slot}
    lesson_days = defaultdict(lambda: defaultdict(int))     # (class_id, lesson_id) -> {day}
    room_busy = defaultdict(int)       # slot -> used rooms
    load_by_day = defaultdict(lambda: defaultdict(int))  # (class) -> day -> hard count
    grid = defaultdict(dict)
    steps = [0]

    def candidates(cid, l):
        out = []
        for s in slots:
            d = s[0]
            if s in class_busy[cid]:
                continue
            if l.get("teacher") is not None:
                if s in teacher_busy[l["teacher"]]:
                    continue
                if teacher_free is not None and s not in teacher_free.get(l["teacher"], set()):
                    continue
            if lesson_days[(cid, l["id"])][d] >= l.get("max_per_day", 1):
                continue
            if l.get("workshop") and room_busy[s] >= workshop_rooms:
                continue
            # پخش سخت‌ها: روزهای با بار سخت کمتر امتیاز بهتر دارند
            hard_load = load_by_day[cid][d]
            score = hard_load * (3 if is_hard(l) else 1) + (periods.index(s[1]) * 0.01)
            out.append((score, s))
        out.sort()
        return [s for _, s in out]

    def place(i):
        steps[0] += 1
        if steps[0] > max_steps:
            return False
        if i == len(tasks):
            return True
        cid, l, _ = tasks[i]
        for s in candidates(cid, l):
            d = s[0]
            class_busy[cid].add(s); lesson_days[(cid, l["id"])][d] += 1
            if l.get("teacher") is not None: teacher_busy[l["teacher"]].add(s)
            if l.get("workshop"): room_busy[s] += 1
            if is_hard(l): load_by_day[cid][d] += 1
            grid[cid][s] = l["id"]
            if place(i + 1):
                return True
            # برگشت
            del grid[cid][s]
            class_busy[cid].discard(s); lesson_days[(cid, l["id"])][d] -= 1
            if l.get("teacher") is not None: teacher_busy[l["teacher"]].discard(s)
            if l.get("workshop"): room_busy[s] -= 1
            if is_hard(l): load_by_day[cid][d] -= 1
        return False

    import sys
    sys.setrecursionlimit(max(10000, len(tasks) * 4))
    ok = place(0)
    if not ok:
        reasons = diagnose(classes, days, periods, teacher_free, workshop_rooms, period_hours)
        if steps[0] > max_steps:
            head = "زمان جست‌وجو برای این محدودیت‌ها تمام شد؛ محدودیت‌ها زیاد یا سخت‌اند."
        else:
            head = "برنامه‌ای که همه‌ی محدودیت‌ها را برآورده کند پیدا نشد."
        return {"ok": False, "grid": {}, "stats": {"steps": steps[0]},
                "errors": [head] + reasons + ([] if reasons else ["علت دقیق پیدا نشد؛ ساعت حضور دبیران، ساعت‌های هفتگی و تعداد کارگاه‌ها را بررسی کنید."])}
    return {"ok": True, "grid": {cid: dict(v) for cid, v in grid.items()}, "errors": [], "stats": {"steps": steps[0], "tasks": len(tasks)}}


def check_grid(classes, grid, teacher_free=None, workshop_rooms=0):
    """بررسی یک برنامه (مثلاً پس از ویرایش دستی) و برگرداندن تداخل‌ها."""
    problems = []
    teacher_at = defaultdict(list)
    room_at = defaultdict(int)
    by_id = {c["id"]: c for c in classes}
    for cid, cells in grid.items():
        lessons = {l["id"]: l for l in by_id[cid]["lessons"]}
        per_day = defaultdict(list)
        for s, lid in cells.items():
            l = lessons[lid]
            per_day[(lid, s[0])].append(s)
            if l.get("teacher") is not None:
                teacher_at[(l["teacher"], s)].append(cid)
                if teacher_free is not None and s not in teacher_free.get(l["teacher"], set()):
                    problems.append(f"دبیر «{l['name']}» در زنگ {s[1]} روز {s[0]} حاضر نیست.")
            if l.get("workshop"):
                room_at[s] += 1
        for (lid, d), ss in per_day.items():
            if len(ss) > l.get("max_per_day", 1):
                problems.append(f"درس «{lessons[lid]['name']}» در کلاس {by_id[cid]['name']} بیشتر از حد مجاز روزانه ({lessons[lid].get('max_per_day', 1)} بار) در روز {d} است.")
    for (t, s), cids in teacher_at.items():
        if len(cids) > 1:
            problems.append(f"تداخل دبیر در روز {s[0]} زنگ {s[1]} (کلاس‌های {len(cids)}).")
    for s, n in room_at.items():
        if n > workshop_rooms:
            problems.append(f"تداخل کارگاه در روز {s[0]} زنگ {s[1]}: {n} کلاس، {workshop_rooms} کارگاه.")
    return problems


def diagnose(classes, days, periods, teacher_free, workshop_rooms, period_hours):
    """علت‌های قابل‌فهم: کلاس‌هایی که ساعت‌شان از زنگ‌های هفته بیشتر است، درس‌هایی که با سقف روزانه جا نمی‌شوند،
    دبیرانی که ساعت حضورشان کمتر از ساعت درسشان است، و درس‌های کارگاهی بیشتر از ظرفیت."""
    out = []
    per_day = {p: (period_hours or {}).get(p, 1) for p in periods}
    week_slots = [(d, p) for d in days for p in periods]
    week_hours = sum(per_day[p] for d in days for p in periods)
    for c in classes:
        need = sum(int(l["hours"]) for l in c["lessons"])
        if need > week_hours:
            out.append(f"کلاس «{c['name']}»: {need} ساعت لازم است ولی زنگ‌های هفته {week_hours} ساعت است.")
        for l in c["lessons"]:
            cap = len(days) * int(l.get("max_per_day", 1) or 1)
            if int(l["hours"]) > cap:
                out.append(f"درس «{l['name']}» در کلاس «{c['name']}»: {l['hours']} ساعت لازم است ولی با سقف روزانه‌ی {l.get('max_per_day', 1)} ساعت فقط {cap} ساعت جا می‌شود.")
    teachers = {}
    for c in classes:
        for l in c["lessons"]:
            if l.get("teacher") is not None:
                teachers.setdefault(l["teacher"], [0, l.get("teacher_name") or f"شماره {l['teacher']}"])
                teachers[l["teacher"]][0] += int(l["hours"])
    for t, (hours, name) in teachers.items():
        free = len(teacher_free.get(t, set())) if teacher_free is not None else len(week_slots)
        if hours > free:
            out.append(f"دبیر {name}: {hours} ساعت درس دارد ولی فقط {free} زنگ حضور ثبت‌شده دارد؛ ساعت حضور او را کامل کنید.")
    if workshop_rooms is not None:
        ws = sum(1 for c in classes for l in c["lessons"] if l.get("workshop"))
        if ws and workshop_rooms <= 0:
            out.append(f"{ws} درس کارگاهی هست ولی هیچ کارگاهی تعریف نشده است.")
    return out
