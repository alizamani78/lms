"""موتور جای‌گذاری با وزن زنگ (زنگ‌های ۱ ساعته، ۲ ساعته، …).

قواعد:
- ابتدا دروس با ساعت بیشتر، سپس دروس با ساعت کمتر (هر درس جلسه‌هایش کنار هم).
- هر زنگ به اندازه‌ی ساعت خودش ظرفیت دارد. درس ۳ ساعته روی زنگ‌های ۲ ساعته = یک جلسه‌ی ۲ ساعته + یک جلسه‌ی ۱ ساعته.
- جلسه‌های ۱ ساعته‌ی دروس مختلف در یک زنگ ۲ ساعته کنار هم می‌نشینند (زنگ خالی ۲ ساعته = دو درس ۱ ساعته).
- معلم در هر زنگ فقط در یک کلاس است و در روزهای مجاز خودش (بر اساس کل ساعتش) تدریس می‌کند؛ روزهای قبلی‌اش ترجیح داده می‌شوند و زنگ‌های نزدیک هم.
- ساعتی که جا نشود برای هر درس «جاگرفته / لازم» و علت گزارش می‌شود؛ ساعت خالی زنگ‌ها «فاقد درس» است.

ورودی و خروجی مانند solve() است، به‌علاوه‌ی «shared» (زنگ‌های مشترک) و «unassigned» (ساعت خالی) و «lesson_status»."""
from collections import defaultdict


def max_days_for(hours):
    """حداکثر روز تدریس دبیر بر اساس کل ساعت هفتگی: تا ۸ = ۱ روز؛ تا ۱۶ = ۲ روز؛ تا ۲۴ = ۳ روز؛ بیشتر = ۴ روز."""
    if hours <= 8:
        return 1
    if hours <= 16:
        return 2
    if hours <= 24:
        return 3
    return 4


def _reason(lesson, teacher_free, slots, cap, cid, teacher_days=None):
    """علت جانگرفتن ساعتی از یک درس، به زبان قابل فهم."""
    t0 = lesson.get("teacher")
    if teacher_days is not None and t0 is not None and teacher_days.get(t0):
        free_on_teacher_days = [sl for sl in slots if sl[0] in teacher_days[t0] and cap[cid][sl] >= 1]
        if not free_on_teacher_days:
            nm = lesson.get("teacher_name") or f"شماره {t0}"
            return (f"روزهای مجاز دبیر {nm} در این کلاس پر شده است؛ برای جابه‌جایی دستی، "
                    f"یک زنگ آزاد در یکی از روزهای او پیدا کنید یا ساعت‌های این کلاس را کم کنید.")
    t = lesson.get("teacher")
    name = lesson.get("teacher_name") or (f"شماره {t}" if t is not None else "")
    if t is None:
        return "دبیر ندارد؛ ابتدا دبیر درس را تعیین کنید."
    if teacher_free is not None and not teacher_free.get(t):
        return f"دبیر {name} هیچ زنگ آزادی ندارد؛ ساعت حضور او را در «ساعات حضور دبیر» بررسی کنید."
    if not [sl for sl in slots if cap[cid][sl] >= 1]:
        return "زنگ‌های این کلاس در هفته پر است."
    return f"زنگ‌های آزاد این کلاس با ساعت حضور دبیر {name} و سقف روزهای او جور نشد."


def solve_weighted(classes, days, periods, period_hours, teacher_free=None, initial=None, max_day_hours=8, external_hours=None):
    slots = [(d, p) for d in days for p in periods]
    weight = {s: int(period_hours.get(s[1], 1)) for s in slots}
    cap = {c["id"]: {s: weight[s] for s in slots} for c in classes}
    teacher_busy = defaultdict(set)     # دبیر -> زنگ‌هایی که در آن تدریس دارد
    teacher_days = defaultdict(set)     # دبیر -> روزهایی که تدریس دارد
    teacher_day_hours = defaultdict(lambda: defaultdict(int))  # دبیر -> روز -> ساعت تدریس آن روز (همه‌ی زنگ‌ها)
    for (t_ext, wd_ext), h_ext in (external_hours or {}).items():
        teacher_day_hours[t_ext][wd_ext] += h_ext   # ساعت دبیر در مدرسه‌های دیگر در سقف روزانه حساب می‌شود
    teacher_hours = defaultdict(int)
    for c in classes:
        for l in c["lessons"]:
            if l.get("teacher") is not None:
                teacher_hours[l["teacher"]] += int(l["hours"])
    grid = defaultdict(dict)
    shared = defaultdict(lambda: defaultdict(list))
    extra = []                          # جلسه‌هایی که روز اضافه‌ی دبیر دارند: (کلاس، روز، زنگ، درس)
    need, placed = {}, defaultdict(int)
    lesson_day = defaultdict(set)       # (کلاس، درس) -> روزهایی که جلسه‌ی ۲ ساعته دارد

    def day_ok(t, d):
        if t is None or d in teacher_days[t]:
            return True
        return len(teacher_days[t]) < max_days_for(teacher_hours[t])

    def teacher_ok(t, s):
        if t is None:
            return True
        if s in teacher_busy[t]:
            return False
        return teacher_free is None or s in teacher_free.get(t, set())

    def hours_ok(t, d, size):
        """سقف ساعت روزانه‌ی دبیر (پیش‌فرض ۸ ساعت) در همه‌ی مدرسه‌ها؛ ساعت‌های همین اجرا و قبلی حساب می‌شوند."""
        return t is None or teacher_day_hours[t][d] + size <= max_day_hours

    def fits(t, s, d, size=1):
        return day_ok(t, d) and teacher_ok(t, s) and hours_ok(t, d, size)

    new_pieces = []                     # جلسه‌هایی که در این اجرا اضافه شده‌اند (نه آنچه از قبل بود)

    def place(cid, li, l, s, size, record=True):
        t = l.get("teacher")
        cap[cid][s] -= size
        if t is not None:
            teacher_busy[t].add(s)
            teacher_days[t].add(s[0])
            teacher_day_hours[t][s[0]] += size
        key = l.get("id", l["name"])
        if not grid[cid].get(s):
            grid[cid][s] = key
        shared[cid][s].append((key, size))
        placed[(cid, li)] += size
        if record:
            new_pieces.append({"class": cid, "day": s[0], "period": s[1], "lesson": key, "size": size})

    def bell_order(t, cid=None, difficulty=3):
        """ترتیب زنگ‌ها: روزهای دبیر؛ سپس روزهایی که این کلاس زودتر پر شده (تا روزهای خالی برای درس‌های سنگین بماند)؛ سپس زنگ‌های نزدیک."""
        def _k(s):
            d, p = s
            used_days = teacher_days[t] if t is not None else set()
            near = min([abs(p - q) for (dd, q) in teacher_busy[t] if dd == d] or [99]) if t is not None else 99
            load = sum(1 for (sl, lst) in shared[cid].items() if sl[0] == d) if cid is not None else 0
            # a teacher with a full load (more than 8 hours) needs an empty day: such a teacher takes the emptiest day;
            # others pack into the days already in use, which leaves whole days free for the heavy teachers
            heavy = t is not None and teacher_hours[t] >= 8
            # سختی درس: درس سخت (۴ و ۵) در ساعت‌های اول روز، درس آسان (۱ و ۲) در ساعت‌های آخر روز
            diff_key = p if difficulty >= 4 else (-p if difficulty <= 2 else 0)
            return (0 if d in used_days else 1, diff_key, load if heavy else -load, near, d, p)
        return _k

    # the placements already in the draft: they keep their places and count toward the lessons' hours
    for x in (initial or []):
        cid, li, l, s = x["class"], x["li"], x["lesson"], (x["day"], x["period"])
        if cap[cid][s] >= x["size"]:
            place(cid, li, l, s, x["size"], record=False)

    # the lessons: more hours first, then by name; each lesson's pieces are placed together
    order = []
    for c in classes:
        for li, l in enumerate(c["lessons"]):
            need[(c["id"], li)] = int(l["hours"])
            order.append((c["id"], li, l))
    # ترتیب: اول دروس ۳ ساعته (ظاهراً ۲ + یک ساعت که در حافظه می‌ماند تا با تک‌ساعت‌ها جفت شود)،
    # سپس بقیه‌ی دروس با ساعت بیشتر اول؛ جلسه‌های ۱ ساعته‌ی باقی‌مانده در مرحله‌ی جفت‌سازی جا می‌گیرند.
    def _group(h):
        return 0 if (h >= 3 and h % 2 == 1) else 1
    order.sort(key=lambda x: (_group(int(x[2]["hours"])), -int(x[2]["hours"]), str(x[2].get("name", ""))))

    # ۱) جلسه‌های ۲ ساعته در زنگ‌های ۲ ساعته (هر درس حداکثر یک جلسه‌ی ۲ ساعته در روز)
    for cid, li, l in order:
        t = l.get("teacher")
        # the two-hour pieces still needed (what is already placed, e.g. from an earlier draft, is not placed again)
        for _ in range(max(0, (need[(cid, li)] - placed[(cid, li)]) // 2)):
            for s in sorted(slots, key=bell_order(t, cid, int(l.get("difficulty", 3)))):
                d = s[0]
                if weight[s] < 2 or cap[cid][s] < 2:
                    continue
                if not fits(t, s, d, 2):   # the teacher's day limit and free bells decide (a lesson may have several 2-hour bells in one day)
                    continue
                place(cid, li, l, s, 2)
                break

    # ۲) جلسه‌های ۱ ساعته در زنگ‌های خالی ۲ ساعته: دو درس ۱ ساعته کنار هم (دو دبیر متفاوت)
    def rem(cid, li):
        return need[(cid, li)] - placed[(cid, li)]

    for cid in cap:
        lessons = [(li, l) for c in classes if c["id"] == cid for li, l in enumerate(c["lessons"])]
        for s in sorted(slots, key=bell_order(None, cid)):
            if weight[s] < 2 or cap[cid][s] != weight[s]:
                continue
            while True:
                cands = [(li, l) for li, l in lessons if rem(cid, li) >= 1 and fits(l.get("teacher"), s, s[0], 1)]
                pair = None
                for i in range(len(cands)):
                    for j in range(i + 1, len(cands)):
                        ta, tb = cands[i][1].get("teacher"), cands[j][1].get("teacher")
                        if ta is not None and ta == tb:
                            continue
                        pair = (cands[i], cands[j])
                        break
                    if pair:
                        break
                if not pair:
                    break
                for li, l in pair:
                    place(cid, li, l, s, 1)
                if cap[cid][s] < 1:
                    break

    # ۳) ساعت‌های باقی‌مانده: جلسه‌ی ۱ ساعته در هر زنگ آزاد (تک‌ها)
    for cid in cap:
        lessons = [(li, l) for c in classes if c["id"] == cid for li, l in enumerate(c["lessons"])]
        for s in sorted(slots, key=bell_order(None, cid)):
            while cap[cid][s] >= 1:
                pick = None
                for li, l in sorted(lessons, key=lambda x: -rem(cid, x[0])):
                    if rem(cid, li) < 1:
                        continue
                    if not fits(l.get("teacher"), s, s[0], 1):
                        continue
                    pick = (li, l)
                    break
                if pick is None:
                    break
                place(cid, pick[0], pick[1], s, 1)

    # ۳ب) دروس کم‌ساعت (تا ۳ ساعت) که فقط به‌خاطر سقف روزهای دبیر جا نشده‌اند: یک روز دیگر از دبیر انتخاب می‌شود.
    # ساعت حضور دبیر (teacher_free) همچنان رعایت می‌شود.
    for cid in cap:
        lessons = [(li, l) for c in classes if c["id"] == cid for li, l in enumerate(c["lessons"])]
        for s in sorted(slots, key=bell_order(None, cid)):
            while cap[cid][s] >= 1:
                pick = None
                for li, l in sorted(lessons, key=lambda x: -rem(cid, x[0])):
                    if rem(cid, li) < 1 or need[(cid, li)] > 3:
                        continue
                    t = l.get("teacher")
                    if not teacher_ok(t, s) or not hours_ok(t, s[0], 1):
                        continue
                    pick = (li, l)
                    break
                if pick is None:
                    break
                t_pick = pick[1].get("teacher")
                if t_pick is not None and s[0] not in teacher_days[t_pick] and len(teacher_days[t_pick]) >= max_days_for(teacher_hours[t_pick]):
                    extra.append({"class": cid, "day": s[0], "period": s[1], "lesson": pick[1].get("id", pick[1]["name"])})
                place(cid, pick[0], pick[1], s, 1)

    # ۴) گزارش: ساعت خالی زنگ‌ها و ساعت جانگرفته‌ی هر درس با علت
    unassigned = []
    for cid in cap:
        for s in slots:
            if cap[cid][s] >= 1:
                unassigned.append({"class": cid, "day": s[0], "period": s[1], "hours": cap[cid][s]})
    errors, status = [], []
    for c in classes:
        for li, l in enumerate(c["lessons"]):
            missing = rem(c["id"], li)
            status.append({"class": c["id"], "lesson": l.get("name"), "required": need[(c["id"], li)],
                           "placed": placed[(c["id"], li)], "missing": max(0, missing)})
            if missing > 0:
                errors.append(f"درس «{l['name']}»: {missing} از {need[(c['id'], li)]} ساعت جا داده نشد. "
                              f"{_reason(l, teacher_free, slots, cap, c['id'], teacher_days)}")
    return {"ok": not errors, "grid": {cid: dict(v) for cid, v in grid.items()},
            "shared": {cid: dict(v) for cid, v in shared.items()},
            "unassigned": unassigned, "errors": errors, "lesson_status": status, "extra_days": extra,
            "new_pieces": new_pieces,
            "stats": {"lessons": len(order)}}
