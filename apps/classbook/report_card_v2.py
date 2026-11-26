"""کارنامه‌ی دوره‌ای و نیم‌سالی (مطابق نمونه‌ی کارنامه‌ی درون‌آموزشگاهی).

داده‌ها از سامانه:
  معدل درس      = میانگین درصد نمره‌های دوره‌ی انتخابی (سرکلاسی، ماهانه، آزمون)، از ۲۰
  معدل کل       = میانگین معدل درس‌ها
  رتبه در کلاس  = جایگاه دانش‌آموز بین همه‌ی دانش‌آموزان همان کلاس بر اساس معدل کل
  رتبه در پایه  = جایگاه بین همه‌ی دانش‌آموزان همان پایه (سال جاری)
  نمره‌ی کیفی   = برچسب بر اساس معدل (آستانه‌ها در QUALITY)
  انضباط        = برچسب بر اساس امتیاز انضباطی باقی‌مانده (آستانه‌ها در DISCIPLINE_LABELS)
  نمودار معدل ماهانه = میانگین کل هر ماه شمسی دوره؛ خط = میانگین کلاس
"""
import html
from collections import defaultdict

from apps.academics.models import ClassRoom
from apps.core.jalali import gregorian_to_jalali
from apps.courses.models import Course
from apps.evaluation.models import ClassGrade

from . import discipline_report
from .gradesheets import TERM_MONTHS

QUALITY = [(19, "خیلی خوب"), (17, "خوب"), (14, "متوسط"), (0, "ضعیف")]
DISCIPLINE_LABELS = [(19, "خیلی خوب"), (17, "خوب"), (14, "متوسط"), (0, "ضعیف")]
MONTH_NAMES = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
               "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def _label(v, table):
    if v is None:
        return ""
    for th, name in table:
        if v >= th:
            return name
    return ""


def _in_period(d, months):
    return d is None or gregorian_to_jalali(d.year, d.month, d.day)[1] in months


def _pct(g):
    mx = float(g.max_score or 0)
    return float(g.score) / mx * 100 if mx > 0 else None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def build(student, months, teacher_id=None):
    cls = ClassRoom.objects.filter(students=student, academic_year__is_current=True).select_related("grade", "school").first()
    members = list(cls.students.filter(role="student")) if cls else [student]
    peers = list(ClassRoom.objects.filter(grade=cls.grade, academic_year=cls.academic_year, school=cls.school)
                 .values_list("students", flat=True)) if cls else []
    grade_ids = {p for p in peers if p} | {m.pk for m in members}
    courses = list(Course.objects.filter(classroom=cls) if cls else Course.objects.filter(enrollments__student=student).distinct())
    if teacher_id:
        courses = [c for c in courses if c.teacher_id == int(teacher_id)]
    cids = [c.pk for c in courses]
    ids = [m.pk for m in members]
    grades = ClassGrade.objects.filter(course_id__in=cids, student_id__in=list(grade_ids | set(ids)))
    per_sc = defaultdict(list)     # (student, course) -> percents
    per_month = defaultdict(list)  # (student, month) -> percents
    for g in grades:
        if not _in_period(g.date, months):
            continue
        p = _pct(g)
        if p is None:
            continue
        per_sc[(g.student_id, g.course_id)].append(p)
        if g.date:
            per_month[(g.student_id, gregorian_to_jalali(g.date.year, g.date.month, g.date.day)[1])].append(p)
    # the term mark of each course by the confirmed rule (monthly, continuous, final: (مستمر×1 + پایانی×2)÷۳)
    from .grade_rules import TERM_MONTHS
    from .score_engine import course_term_grade
    term = "دوم" if set(months) == set(TERM_MONTHS["دوم"]) else "اول"
    course_avg = {}
    for c in courses:
        for sid in set(ids) | set(grade_ids):
            r = course_term_grade(c, sid, term, months)
            if r["term"] is not None:
                course_avg[(sid, c.pk)] = float(r["term"])
    cs = {}
    for c in courses:
        vals = {}
        for m in members:
            a = course_avg.get((m.pk, c.pk))
            if a is not None:
                vals[m.pk] = a
        cs[c.pk] = vals
    total_of = {}
    for sid in grade_ids:
        vals = [course_avg[(sid, c.pk)] for c in courses if (sid, c.pk) in course_avg]
        total_of[sid] = _avg(vals)
    class_rank = {}
    for sid in ids:
        v = total_of.get(sid)
        class_rank[sid] = 1 + sum(1 for m in ids if total_of.get(m) is not None and v is not None and total_of[m] > v) if v is not None else None
    grade_rank = {}
    for sid in ids:
        v = total_of.get(sid)
        grade_rank[sid] = 1 + sum(1 for m in grade_ids if total_of.get(m) is not None and v is not None and total_of[m] > v) if v is not None else None
    rows = []
    for c in courses:
        mine = course_avg.get((student.pk, c.pk))
        cls_avg = _avg(list(cs[c.pk].values()))
        rank_in_class = 1 + sum(1 for x in cs[c.pk].values() if mine is not None and x > mine) if mine is not None else None
        rows.append({"course": c.title, "teacher": (c.teacher.get_full_name() or c.teacher.username) if c.teacher_id else "",
                     "avg": mine, "class_avg": cls_avg, "quality": _label(mine, QUALITY), "rank_class": rank_in_class})
    month_avg = {}
    class_month = defaultdict(list)
    for (sid, mo), vals in per_month.items():
        a = _avg([x * 0.2 for x in vals])
        if sid == student.pk:
            month_avg[mo] = a
        if sid in ids:
            class_month[mo].append(a)
    class_month_avg = {mo: _avg(v) for mo, v in class_month.items()}
    dis = discipline_report.build(student, None)
    top_class = sorted([(total_of.get(m.pk), m) for m in members if total_of.get(m.pk) is not None], key=lambda x: -x[0])[:3]
    top_grade = sorted([(total_of.get(p), p) for p in grade_ids if total_of.get(p) is not None], key=lambda x: -x[0])[:3]
    grade_pupils = {p: ClassRoom.objects.filter(students__pk=p, academic_year=cls.academic_year).values_list("name", flat=True).first() if cls else ""
                    for p in grade_ids}
    return {"student": student, "cls": cls, "rows": rows, "overall": total_of.get(student.pk),
            "class_rank": class_rank.get(student.pk), "grade_rank": grade_rank.get(student.pk),
            "class_size": len(ids), "grade_size": len(grade_ids), "quality": _label(total_of.get(student.pk), QUALITY),
            "discipline_label": _label(float(dis["remaining"]) if dis["remaining"] is not None else None, DISCIPLINE_LABELS),
            "discipline": dis, "months": {mo: month_avg.get(mo) for mo in sorted(month_avg)},
            "class_months": {mo: class_month_avg.get(mo) for mo in sorted(month_avg)},
            "top_class": [(v, m) for v, m in top_class], "top_grade": [(v, p, grade_pupils.get(p)) for v, p in top_grade]}


def _photo_uri(user):
    """عکس دانش‌آموز از فضای خصوصی سامانه، به‌صورت داده‌ی درون‌خطی (برای چاپ بدون ورود دوباره)."""
    import base64
    from pathlib import Path
    from apps.accounts.photos import storage_root
    if not getattr(user, "photo_id", None):
        return ""
    path = Path(storage_root()) / user.photo.stored_name[:2] / user.photo.stored_name
    if not path.is_file():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _bars_line(title, labels, bars, line, vmax=20, vmin=8):
    w, h, pad = 560, 240, 34
    n = max(1, len(labels))
    bw = (w - 2 * pad) / n
    def y(v):
        v = max(vmin, min(vmax, float(v)))
        return h - (v - vmin) / (vmax - vmin) * (h - 40)
    out = [f"<svg viewBox='0 0 {w} {h+50}' width='100%' xmlns='http://www.w3.org/2000/svg'>",
           f"<text x='{w/2}' y='16' text-anchor='middle' font-size='13' font-weight='700'>{html.escape(title)}</text>"]
    for i, (lab, b) in enumerate(zip(labels, bars)):
        if b is None:
            continue
        x = pad + i * bw + bw * 0.2
        top = y(b)
        out.append(f"<rect x='{x:.1f}' y='{top:.1f}' width='{bw*0.6:.1f}' height='{h-top:.1f}' fill='#4ade80' stroke='#166534'/>")
        out.append(f"<text x='{x+bw*0.3:.1f}' y='{top-4:.1f}' text-anchor='middle' font-size='9'>{b}</text>")
        out.append(f"<text x='{x+bw*0.3:.1f}' y='{h+12}' text-anchor='middle' font-size='9' transform='rotate(-30 {x+bw*0.3:.1f} {h+12})'>{html.escape(str(lab)[:16])}</text>")
    pts = [(pad + i * bw + bw * 0.5, y(v)) for i, v in enumerate(line) if v is not None]
    if len(pts) > 1:
        out.append("<polyline fill='none' stroke='#dc2626' stroke-width='1.2' points='" + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + "'/>")
    for a, b in pts:
        out.append(f"<rect x='{a-2.5:.1f}' y='{b-2.5:.1f}' width='5' height='5' fill='#dc2626'/>")
    out.append(f"<line x1='{pad}' y1='{h}' x2='{w-pad}' y2='{h}' stroke='#333'/></svg>")
    return "".join(out)


CSS = """
@font-face{font-family:"VazirRC";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirRC";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:8mm}
html,body{font-family:"VazirRC",Tahoma,sans-serif;color:#000;font-size:10.5px;line-height:1.5;margin:0;background:#fff}
table{width:100%;border-collapse:collapse}th,td{border:1px solid #000;padding:2px 4px;text-align:center;vertical-align:middle}
th{background:#e6e6e6;font-weight:800}td.r{text-align:right}
.hd{display:flex;gap:6px;align-items:stretch;margin-bottom:4px}.hd>div{border:1px solid #000;padding:3px 6px}
.box{border:1px solid #000;padding:4px 6px;text-align:center}
.charts{display:grid;grid-template-columns:1.6fr 1fr;gap:6px;margin-top:6px}.chart{border:1px solid #000;padding:3px}
.tops{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:6px}
.note{border:1px solid #000;padding:6px;margin-top:6px}
"""


def render_html(d, period_title):
    s = d["student"]
    name = html.escape(s.get_full_name() or s.username)
    cls_name = d["cls"].name if d["cls"] else ""
    grade_name = d["cls"].grade.name if d["cls"] else ""
    field = d["cls"].study_field.name if d["cls"] and d["cls"].study_field_id else ""
    rows = []
    for i, r in enumerate(d["rows"], start=1):
        rows.append(f"<tr><td>{i}</td><td class='r'>{html.escape(r['course'])}</td><td>{html.escape(r['teacher'])}</td>"
                    f"<td>{'' if r['avg'] is None else r['avg']}</td><td>{'' if r['class_avg'] is None else r['class_avg']}</td>"
                    f"<td>{html.escape(r['quality'])}</td><td>{'' if r['rank_class'] is None else r['rank_class']}</td></tr>")
    table = ("<table><thead><tr><th>ردیف</th><th>درس</th><th>نام دبیر</th><th>معدل درس</th><th>معدل کلاس</th><th>نمره‌ی کیفی</th><th>رتبه در کلاس</th></tr></thead>"
             f"<tbody>{''.join(rows) or '<tr><td colspan=7>درسی ثبت نشده است.</td></tr>'}</tbody></table>")
    months = d["months"]
    mlabels = [MONTH_NAMES[m - 1] for m in months] or ["—"]
    bars = list(months.values()) or [None]
    line = list(d["class_months"].values()) or [None]
    c1 = _bars_line("نمودار میله‌ای معدل درس‌های دانش‌آموز و خط میانگین کلاس", [r["course"] for r in d["rows"]] or ["—"],
                    [r["avg"] for r in d["rows"]] or [None], [r["class_avg"] for r in d["rows"]] or [None])
    c2 = _bars_line("نمودار پیشرفت معدل ماهانه", mlabels, bars, line)
    top_c = "".join(f"<tr><td>{i}</td><td>{html.escape((m.get_full_name() or m.username))}</td><td>{v}</td></tr>"
                    for i, (v, m) in enumerate(d["top_class"], start=1)) or "<tr><td colspan=3>—</td></tr>"
    top_g = "".join(f"<tr><td>{i}</td><td>{html.escape((p.get_full_name() or p.username))}</td><td>{html.escape(str(c or ''))}</td><td>{v}</td></tr>"
                    for i, (v, p, c) in enumerate(d["top_grade"], start=1)) or "<tr><td colspan=4>—</td></tr>"
    dis = d["discipline"]
    photo = _photo_uri(s)
    photo_html = (f"<img src='{photo}' alt='' style='width:26mm;height:32mm;object-fit:cover;border:1px solid #000'>"
                  if photo else "<div style='width:26mm;height:32mm;border:1px solid #000;font-size:9px'>بدون عکس</div>")
    body = (f"<div class='hd'>{photo_html}<div style='flex:1'><b>نام آموزشگاه:</b> {html.escape(d['cls'].school.name if d['cls'] else '')}</div>"
            f"<div style='flex:1'><b>نام و نام خانوادگی:</b> {name}</div><div><b>کلاس:</b> {html.escape(cls_name)}</div>"
            f"<div><b>پایه / رشته:</b> {html.escape(grade_name)} {html.escape(field)}</div></div>"
            f"<div class='box'><b>{html.escape(period_title)}</b></div>"
            f"{table}"
            f"<div class='hd' style='margin-top:6px'><div class='box'><b>معدل کل</b><br>{'' if d['overall'] is None else d['overall']}</div>"
            f"<div class='box'><b>رتبه در کلاس</b><br>{'' if d['class_rank'] is None else d['class_rank']} از {d['class_size']}</div>"
            f"<div class='box'><b>رتبه در پایه</b><br>{'' if d['grade_rank'] is None else d['grade_rank']} از {d['grade_size']}</div>"
            f"<div class='box'><b>انضباط دانش‌آموز</b><br>{html.escape(d['discipline_label'] or '—')}</div>"
            f"<div style='flex:1'><b>امتیاز انضباطی باقی‌مانده:</b> {dis['remaining']} از {dis['base']} — جمع کسرها: {dis['total']}</div></div>"
            f"<div class='charts'><div class='chart'>{c1}</div><div class='chart'>{c2}</div></div>"
            f"<div class='tops'><table><thead><tr><th>رتبه</th><th>نام و نام خانوادگی</th><th>معدل</th></tr></thead><tbody>{top_c}</tbody></table>"
            f"<table><thead><tr><th>رتبه</th><th>نام و نام خانوادگی</th><th>کلاس</th><th>معدل</th></tr></thead><tbody>{top_g}</tbody></table></div>"
            f"<div class='note'>ولی محترم دانش‌آموز: لطفاً چنانچه پیشنهاد و یا انتقادی دارید، پشت برگه‌ی کارنامه مرقوم فرمایید. با تشکر — مدیر آموزشگاه</div>")
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>کارنامه</title><style>{CSS}</style></head><body>{body}</body></html>")


def render_xlsx(d, period_title):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "کارنامه"
    ws.sheet_view.rightToLeft = True
    s = d["student"]
    ws.append([period_title, s.get_full_name() or s.username, s.student_code or ""])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["معدل کل", d["overall"], "رتبه در کلاس", d["class_rank"], "رتبه در پایه", d["grade_rank"]])
    ws.append([])
    ws.append(["درس", "دبیر", "معدل درس", "معدل کلاس", "نمره‌ی کیفی", "رتبه در کلاس"])
    for c in ws[4]:
        c.font = Font(bold=True)
    for r in d["rows"]:
        ws.append([r["course"], r["teacher"], r["avg"], r["class_avg"], r["quality"], r["rank_class"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

