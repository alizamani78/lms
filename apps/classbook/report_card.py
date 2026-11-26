"""کارنامه‌ی تحصیلی دانش‌آموز: نمره‌ی مستمر و پایانی هر درس، حضور و غیاب، کسرهای انضباطی، و نمودارهای SVG.

  مستمر (از ۲۰)  = میانگین درصد نمره‌های سرکلاسی و ماهانه همان درس × ۰٫۲
  پایانی (از ۲۰) = میانگین درصد نمره‌های آزمون (میان‌ترم و پایان ترم)، و اگر نبود، درصد آزمون‌های سامانه × ۰٫۲
  حضور          = شمارش وضعیت‌ها در همان نیم‌سال (یا کل سال)
  انضباط        = discipline_report (پایه‌ی ۲۰ منهای جمع کسرها)
"""
import html
from collections import defaultdict
from decimal import Decimal

from django.db.models import Q

from apps.accounts.models import User  # noqa: F401
from apps.attendance.models import AttendanceRecord
from apps.courses.models import Course
from apps.evaluation.logic import course_percentages
from apps.evaluation.models import ClassGrade

from . import discipline_report
from .gradesheets import _in_term

STATUS_LABEL = {"present": "حاضر", "absent": "غایب", "late": "تأخیر", "excused": "غیبت موجه", "leave": "مرخصی"}


def _avg(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def build(student, term=None, teacher_id=None):
    courses = list(Course.objects.filter(Q(enrollments__student=student) | Q(classroom__students=student))
                   .select_related("org", "teacher", "classroom__grade").distinct().order_by("title"))
    cids = [c.pk for c in courses]
    org_ids = list({c.org_id for c in courses})
    pct = course_percentages(org_ids, student_ids=[student.pk], course_ids=cids) if courses else {}
    grades = defaultdict(list)
    for g in ClassGrade.objects.filter(student=student, course_id__in=cids):
        if g.date is None or not term or _in_term(g.date, term):
            grades[g.course_id].append(g)
    att = defaultdict(lambda: defaultdict(int))
    for r in AttendanceRecord.objects.filter(student=student, course_id__in=cids):
        if not term or _in_term(r.date, term):
            att[r.course_id][r.status] += 1
    rows = []
    for c in courses:
        gl = grades.get(c.pk, [])
        cont = [float(g.score) / float(g.max_score or 1) * 100 for g in gl if g.period in ("class", "monthly") and float(g.max_score or 0) > 0]
        fin = [float(g.score) / float(g.max_score or 1) * 100 for g in gl if g.period in ("midterm", "final") and float(g.max_score or 0) > 0]
        rec = pct.get((student.pk, c.pk))
        if not fin and rec and (rec[3]):
            fin = list(rec[3])
        cont20 = _avg(cont)
        fin20 = _avg(fin)
        cont20 = round(cont20 * 0.2, 2) if cont20 is not None else None
        fin20 = round(fin20 * 0.2, 2) if fin20 is not None else None
        # for a term, the continuous and final marks are the confirmed rule's values (score_engine)
        if term:
            from .grade_rules import TERM_MONTHS
            from .score_engine import course_term_grade
            term_name = "دوم" if set(term) == set(TERM_MONTHS["دوم"]) else "اول"
            eng = course_term_grade(c, student.pk, term_name, term)
            if eng["continuous"] is not None:
                cont20 = float(eng["continuous"])
            if eng["final"] is not None:
                fin20 = float(eng["final"])
        a = att.get(c.pk, {})
        rows.append({"course": c.title, "teacher": (c.teacher.get_full_name() or c.teacher.username) if c.teacher_id else "",
                     "continuous": cont20, "final": fin20,
                     "attendance": {k: a.get(k, 0) for k in STATUS_LABEL}})
    # the whole year: the two term marks, the annual mark (نمره‌ی سالانه) and the units of each course
    from apps.academics.models import GradeWeeklyHours
    from apps.classbook.grade_rules import TERM_MONTHS, annual_grade, average_by_units
    from apps.classbook.score_engine import course_term_grade
    overall = None
    if not term:
        for r, c in zip(rows, courses):
            t1 = course_term_grade(c, student.pk, "اول", list(TERM_MONTHS["اول"]))
            t2 = course_term_grade(c, student.pk, "دوم", list(TERM_MONTHS["دوم"]))
            r["term1"] = float(t1["term"]) if t1["term"] is not None else None
            r["term2"] = float(t2["term"]) if t2["term"] is not None else None
            ann = annual_grade(t1["continuous"], t1["final"], t2["continuous"], t2["final"])
            r["annual"] = float(ann) if ann is not None else None
            r["units"] = _course_units(c)
        overall = average_by_units([(r["annual"], r["units"]) for r in rows if r.get("annual") is not None])
    else:
        # the term's average: each course's term mark, weighted by its units
        items = []
        for r, c in zip(rows, courses):
            tm = course_term_grade(c, student.pk, "دوم" if set(term) == set(TERM_MONTHS["دوم"]) else "اول", list(term))["term"]
            if tm is not None:
                items.append((tm, _course_units(c)))
        overall = average_by_units(items)
    analysis = _analysis(rows, term)
    if teacher_id:
        rows = [r for r, c in zip(rows, courses) if c.teacher_id == int(teacher_id)]
        courses = [c for c in courses if c.teacher_id == int(teacher_id)]
    dis = discipline_report.build(student, term)
    return {"student": student, "term": term, "rows": rows, "discipline": dis, "overall": overall, "analysis": analysis}


def _course_units(c):
    """واحد درس برای کلاس این درس (از «سختی و واحد دروس»): پایه و رشته، وگرنه عمومی؛ پیش‌فرض ۱."""
    from apps.academics.models import GradeWeeklyHours
    if not (c.classroom_id and c.subject_id):
        return 1
    gw = (GradeWeeklyHours.objects.filter(grade_id=c.classroom.grade_id, subject_id=c.subject_id, field_id=c.classroom.study_field_id).first()
          or GradeWeeklyHours.objects.filter(grade_id=c.classroom.grade_id, subject_id=c.subject_id, field__isnull=True).first())
    return (gw.hours if gw else None) or 1


def _analysis(rows, term):
    """تحلیل خودکار عملکرد: روند نیم‌سال‌ها، درس‌های قوی و ضعیف، و درس‌های در حال افت یا پیشرفت."""
    out = []
    if not term:
        pairs = [(r["course"], r.get("term1"), r.get("term2")) for r in rows if r.get("term1") is not None and r.get("term2") is not None]
        up = [c for c, a, b in pairs if b > a]
        down = [c for c, a, b in pairs if b < a]
        if up:
            out.append("نمره‌ی نیم‌سال دوم در این درس‌ها بالاتر از نیم‌سال اول است: " + "، ".join(up) + ".")
        if down:
            out.append("نمره‌ی نیم‌سال دوم در این درس‌ها پایین‌تر از نیم‌سال اول است: " + "، ".join(down) + "؛ پیگیری توصیه می‌شود.")
    scored = [(r["course"], r.get("annual") if not term else r.get("continuous")) for r in rows]
    scored = [(c, v) for c, v in scored if v is not None]
    if scored:
        best = max(scored, key=lambda x: x[1])
        worst = min(scored, key=lambda x: x[1])
        out.append(f"بالاترین نمره در «{best[0]}» ({best[1]}) و پایین‌ترین در «{worst[0]}» ({worst[1]}) است.")
        weak = [c for c, v in scored if v < 10]
        if weak:
            out.append("درس‌های زیر ۱۰ نیاز به تقویت دارند: " + "، ".join(weak) + ".")
    if not out:
        out.append("نمره‌ی کافی برای تحلیل عملکرد ثبت نشده است.")
    return out


def _bars(title, labels, values, vmax, unit=""):
    w, h, pad = 520, 220, 36
    n = max(1, len(values))
    bw = (w - 2 * pad) / n
    parts = [f"<svg viewBox='0 0 {w} {h + 40}' width='100%' xmlns='http://www.w3.org/2000/svg' role='img'>",
             f"<text x='{w/2}' y='16' text-anchor='middle' font-size='13' font-weight='700'>{html.escape(title)}</text>",
             f"<line x1='{pad}' y1='{h}' x2='{w-pad}' y2='{h}' stroke='#333'/>"]
    for i, (lab, val) in enumerate(zip(labels, values)):
        v = 0 if val is None else float(val)
        bh = (h - 30) * min(v, vmax) / vmax if vmax else 0
        x = pad + i * bw + bw * 0.15
        parts.append(f"<rect x='{x:.1f}' y='{h - bh:.1f}' width='{bw*0.7:.1f}' height='{bh:.1f}' fill='#0f5c4d'/>")
        parts.append(f"<text x='{x + bw*0.35:.1f}' y='{h - bh - 4:.1f}' text-anchor='middle' font-size='10'>{'' if val is None else html.escape(str(val))}{unit}</text>")
        parts.append(f"<text x='{x + bw*0.35:.1f}' y='{h + 14}' text-anchor='middle' font-size='9' transform='rotate(-20 {x + bw*0.35:.1f} {h + 14})'>{html.escape(lab[:18])}</text>")
    parts.append("</svg>")
    return "".join(parts)


def charts(d):
    rows = d["rows"]
    labels = [r["course"] for r in rows] or ["—"]
    fin = [r["final"] for r in rows] or [0]
    cont = [r["continuous"] for r in rows] or [0]
    out = [_bars("نمره‌ی پایانی هر درس (از ۲۰)", labels, fin, 20),
           _bars("نمره‌ی مستمر هر درس (از ۲۰)", labels, cont, 20)]
    att_total = defaultdict(int)
    for r in rows:
        for k, v in r["attendance"].items():
            att_total[k] += v
    out.append(_bars("وضعیت حضور و غیاب (تعداد جلسات)", [STATUS_LABEL[k] for k in STATUS_LABEL],
                     [att_total[k] for k in STATUS_LABEL], max([att_total[k] for k in STATUS_LABEL] + [1])))
    dis = d["discipline"]
    out.append(_bars("امتیاز انضباطی باقی‌مانده", ["پایه", "باقی‌مانده"], [float(dis["base"]), float(dis["remaining"])], float(dis["base"]) or 20))
    return out


CSS = """
@font-face{font-family:"VazirCard";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirCard";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:9mm}
html,body{font-family:"VazirCard",Tahoma,sans-serif;color:#000;font-size:11px;line-height:1.6;margin:0;background:#fff}
.form{border:1.5px solid #000}.ttl{text-align:center;font-weight:800;font-size:14px;padding:6px;border-bottom:1px solid #000}
table{width:100%;border-collapse:collapse;margin-top:6px}th,td{border:1px solid #000;padding:3px 5px;vertical-align:middle}
th{background:#eee;font-weight:800;text-align:center}td.c{text-align:center}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.chart{border:1px solid #999;padding:4px}
"""


def render_html(d):
    s = d["student"]
    name = html.escape(s.get_full_name() or s.username)
    per = f"نیم‌سال {html.escape(d['term'])}" if d["term"] else "کل سال"
    dis = d["discipline"]
    yearly = not d["term"]
    def _v(x):
        return "" if x is None else x
    rows = "".join(
        f"<tr><td>{html.escape(r['course'])}</td><td>{html.escape(r['teacher'])}</td>"
        f"<td class='c'>{_v(r['continuous'])}</td><td class='c'>{_v(r['final'])}</td>"
        + (f"<td class='c'>{_v(r.get('term1'))}</td><td class='c'>{_v(r.get('term2'))}</td><td class='c'><b>{_v(r.get('annual'))}</b></td>" if yearly else "")
        + "".join(f"<td class='c'>{r['attendance'][k]}</td>" for k in STATUS_LABEL) + "</tr>"
        for r in d["rows"]) or "<tr><td colspan='12' class='c'>درسی ثبت نشده است.</td></tr>"
    year_heads = "<th>نیم‌سال اول</th><th>نیم‌سال دوم</th><th>سالانه</th>" if yearly else ""
    analysis_html = ("<div class='analysis'><b>تحلیل عملکرد:</b><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in d.get("analysis", [])) + "</ul>"
                     + (f"<b>معدل {'سالانه' if yearly else 'نیم‌سال'} (با واحد درس):</b> {_v(d.get('overall'))}" if d.get("overall") is not None else "")
                     + "</div>")
    heads = "".join(f"<th>{v}</th>" for v in STATUS_LABEL.values())
    body = (f"<div class='form'><div class='ttl'>کارنامه‌ی تحصیلی دانش‌آموز — {per}</div>"
            f"<table><tr><td><b>نام و نام خانوادگی:</b> {name}</td><td><b>کد دانش‌آموزی:</b> {html.escape(s.student_code or '')}</td></tr>"
            f"<tr><td><b>امتیاز انضباطی باقی‌مانده:</b> {dis['remaining']} از {dis['base']}</td><td><b>جمع کسرهای انضباطی:</b> {dis['total']}</td></tr></table></div>"
            f"<table><thead><tr><th>درس</th><th>دبیر</th><th>مستمر (از ۲۰)</th><th>پایانی (از ۲۰)</th>{year_heads}{heads}</tr></thead><tbody>{rows}</tbody></table>{analysis_html}"
            "<div class='charts'>" + "".join(f"<div class='chart'>{c}</div>" for c in charts(d)) + "</div>")
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>کارنامه</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")


def render_xlsx(d):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "کارنامه"
    ws.sheet_view.rightToLeft = True
    s = d["student"]
    ws.append(["کارنامه‌ی تحصیلی", s.get_full_name() or s.username, s.student_code or "", "نیم‌سال", d["term"] or "کل سال"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(["درس", "دبیر", "مستمر (از ۲۰)", "پایانی (از ۲۰)"] + list(STATUS_LABEL.values()))
    for c in ws[3]:
        c.font = Font(bold=True)
    for r in d["rows"]:
        ws.append([r["course"], r["teacher"], r["continuous"], r["final"]] + [r["attendance"][k] for k in STATUS_LABEL])
    dis = d["discipline"]
    ws.append([])
    ws.append(["انضباطی", "پایه", dis["base"], "جمع کسرها", dis["total"], "باقی‌مانده", dis["remaining"]])
    ws2 = wb.create_sheet("انضباط")
    ws2.sheet_view.rightToLeft = True
    ws2.append(["تاریخ", "شرح", "کسر", "مدرسه", "توضیح"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for r in dis["rows"]:
        ws2.append([r["date"], r["title"], float(r["points"]), r["school"], r["note"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
