"""کارنامه‌ی هشدار: نمودار میله‌ای ماهانه به تفکیک درس (هر ماه یک رنگ، کنار هم)، رتبه‌ی دانش‌آموز در هر ماه
در کلاس، میزان پیشرفت (عدد و نمودار) و توضیحات مدیر."""
import html
from decimal import Decimal

from apps.academics.models import ClassRoom
from apps.accounts.models import User
from apps.courses.models import Course
from apps.evaluation.models import ClassGrade

from .grade_rules import TERM_MONTHS, average_by_units, monthly_average, monthly_value, progress
from .monthly import MONTH_NAMES, _month_start_end

COLORS = {7: "#1f77b4", 8: "#2ca02c", 9: "#ff7f0e", 10: "#9467bd", 11: "#8c564b", 12: "#e377c2",
          1: "#17becf", 2: "#bcbd22", 3: "#d62728", 4: "#7f7f7f", 5: "#393b79", 6: "#8c6d31"}


def _student_courses(student, teacher_id=None):
    cls_ids = list(student.classrooms.values_list("pk", flat=True))
    qs = Course.objects.filter(classroom_id__in=cls_ids).select_related("subject").order_by("title")
    if teacher_id:
        qs = qs.filter(teacher_id=int(teacher_id))
    return list(qs)


def _month_value(course, student, term, jm, today_end=None):
    """نمره‌ی ماهیانه‌ی به‌کاررفته‌ی یک درس در یک ماه (نمره‌ی معلم یا میانگین)."""
    start, end = _month_start_end(term, jm)
    teacher = ClassGrade.objects.filter(course=course, student=student, period="monthly", title=f"ماهانه {MONTH_NAMES[jm]}").first()
    cls = [(g.date, g.score, g.max_score) for g in ClassGrade.objects.filter(course=course, student=student, period="class", date__gte=start, date__lte=end)]
    computed = monthly_average(cls, term, end)
    v = monthly_value(computed, teacher.score if teacher else None)
    return None if v is None else Decimal(v)


def build(student, term, teacher_id=None):
    """داده‌ی کارنامه‌ی هشدار برای یک نیم‌سال: ماه‌ها، درس‌ها، مقدار هر میله، رتبه‌ی ماهانه و پیشرفت.
    با teacher_id فقط درس‌های همان معلم و رتبه‌ی دانش‌آموز در همان درس‌ها (بین هم‌کلاسی‌ها) حساب می‌شود."""
    months = [m for m in TERM_MONTHS[term]]
    courses = _student_courses(student, teacher_id)
    values = {}
    for c in courses:
        for m in months:
            v = _month_value(c, student, term, m)
            if v is not None:
                values[(c.pk, m)] = v
    # the month's average over the courses; then the rank among the class's students
    month_avg = {}
    for m in months:
        vals = [values[(c.pk, m)] for c in courses if (c.pk, m) in values]
        month_avg[m] = average_by_units([(v, Decimal(1)) for v in vals]) if vals else None
    ranks = {}
    cls_ids = list(student.classrooms.values_list("pk", flat=True))
    classmates = list(User.objects.filter(classrooms__in=cls_ids, role="student").distinct())
    for m in months:
        if month_avg.get(m) is None:
            continue
        scored = []
        for s in classmates:
            cs = _student_courses(s, teacher_id)
            vals = [v for c in cs for v in [_month_value(c, s, term, m)] if v is not None]
            if vals:
                scored.append((s.pk, sum(vals) / len(vals)))
        scored.sort(key=lambda x: -x[1])
        pos = [pid for pid, _ in scored].index(student.pk) + 1 if any(pid == student.pk for pid, _ in scored) else None
        ranks[m] = (pos, len(scored))
    prog = {}
    seq = [m for m in months if month_avg.get(m) is not None]
    for a, b in zip(seq, seq[1:]):
        prog[b] = progress(month_avg[b], month_avg[a])
    return {"courses": courses, "months": months, "values": values, "month_avg": month_avg, "ranks": ranks, "progress": prog}


def bars_svg(d):
    """نمودار میله‌ای: برای هر درس، میله‌های ماه‌ها کنار هم با رنگ جدا."""
    courses, months, values = d["courses"], d["months"], d["values"]
    if not courses:
        return "<p>درسی برای نمودار نیست.</p>"
    bw, gap, base, h = 14, 4, 170, 150
    group_w = len(months) * (bw + gap) + 26
    width = max(560, len(courses) * group_w + 40)
    parts = []
    for gi, c in enumerate(courses):
        x0 = 30 + gi * group_w
        for mi, m in enumerate(months):
            v = values.get((c.pk, m))
            if v is None:
                continue
            bh = float(v) / 20 * h
            x = x0 + mi * (bw + gap)
            parts.append(f"<rect x='{x}' y='{base - bh:.1f}' width='{bw}' height='{bh:.1f}' fill='{COLORS[m]}'>"
                         f"<title>{html.escape(c.subject.name if c.subject_id else c.title)} — {MONTH_NAMES[m]}: {v}</title></rect>"
                         f"<text x='{x + bw / 2}' y='{base - bh - 3:.1f}' font-size='8' text-anchor='middle'>{v}</text>")
        parts.append(f"<text x='{x0 + group_w / 2 - 13}' y='{base + 14}' font-size='9' text-anchor='middle'>"
                     f"{html.escape((c.subject.name if c.subject_id else c.title)[:14])}</text>")
    legend = "".join(f"<rect x='{30 + i * 78}' y='{base + 26}' width='10' height='10' fill='{COLORS[m]}'/>"
                     f"<text x='{44 + i * 78}' y='{base + 35}' font-size='9'>{MONTH_NAMES[m]}</text>" for i, m in enumerate(months[:6]))
    axis = f"<line x1='26' y1='{base}' x2='{width - 6}' y2='{base}' stroke='#000'/><line x1='26' y1='{base - h}' x2='26' y2='{base}' stroke='#000'/>"
    return (f"<svg viewBox='0 0 {width} {base + 50}' xmlns='http://www.w3.org/2000/svg' style='width:100%;height:auto'>"
            f"{axis}{''.join(parts)}{legend}</svg>")


def render_fragment(student, term, notes="", teacher_id=None):
    d = build(student, term, teacher_id)
    e = html.escape
    rank_rows = "".join(f"<tr><td>{e(MONTH_NAMES[m])}</td><td>{d['month_avg'][m] if d['month_avg'].get(m) is not None else '—'}</td>"
                        f"<td>{(str(d['ranks'][m][0]) + ' از ' + str(d['ranks'][m][1])) if m in d['ranks'] and d['ranks'][m][0] else '—'}</td>"
                        f"<td>{d['progress'].get(m, '—')}</td></tr>" for m in d["months"] if d["month_avg"].get(m) is not None)
    return (f"<div class='warn-card'><div class='t'>کارنامه‌ی هشدار — نیم‌سال {e(term)}</div>"
            f"<div><b>دانش‌آموز:</b> {e(student.get_full_name() or student.username)} &nbsp; <b>کد:</b> {e(student.student_code or '')}</div>"
            f"{bars_svg(d)}"
            f"<table><tr><th>ماه</th><th>میانگین ماهانه</th><th>رتبه در کلاس</th><th>میزان پیشرفت</th></tr>{rank_rows or '<tr><td colspan=4>نمره‌ای ثبت نشده است.</td></tr>'}</table>"
            f"<div class='notes'><b>توضیحات مدیر:</b> {e(notes or '—')}</div></div>")
