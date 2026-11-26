"""چاپ کاربرگ‌های دفتر فرایندی به شکل فرم‌های رسمی (۶۰۱، ۶۰۲، ۶۰۹)، با فونت و کادربندی سامانه."""
import html

from apps.core.jalali import format_jalali, system_date
from .services import plan_totals, ensure_rows
from .models import Status

CSS = """
@font-face{font-family:"VazirBook";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirBook";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:9mm}
html,body{font-family:"VazirBook",Tahoma,sans-serif;color:#000;font-size:11px;line-height:1.6;margin:0;background:#fff}
.form{border:1.5px solid #000}
.ttl{text-align:center;font-weight:800;font-size:13px;padding:5px;border-bottom:1px solid #000}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #000;padding:3px 4px;vertical-align:top}
th{background:#eee;font-weight:800;text-align:center}
td.c{text-align:center}
.kv td{border:none;border-bottom:1px solid #000;padding:4px 6px}
.stamp{position:fixed;top:45%;left:20%;font-size:42px;color:#bbb;transform:rotate(-25deg);font-weight:800}
.sec{font-weight:800;background:#f3f3f3;border-bottom:1px solid #000;padding:3px 6px}
.blank{height:22px}
.foot{display:flex;justify-content:space-between;margin-top:12px;font-size:11px}
.foot div{width:31%;text-align:center}
.line{border-bottom:1px dotted #000;height:24px;margin-top:4px}
"""


def e(x):
    return html.escape(str(x if x is not None else ""))


def _head(title, course, extra_rows):
    cls = course.classroom
    cls_name = f"{cls.grade.name} — {cls.name}" if cls else ""
    rows = "".join(f"<tr><td style='width:50%'>{e(a)}</td><td>{e(b)}</td></tr>" for a, b in extra_rows)
    return (f"<div class='form'><div class='ttl'>{e(title)}</div>"
            f"<table class='kv'><tr><td><b>نام درس:</b> {e(course.title)}</td><td><b>کلاس:</b> {e(cls_name)}</td></tr>"
            f"<tr><td><b>مدرسه:</b> {e(course.org.name)}</td><td><b>دبیر:</b> {e((course.teacher.get_full_name() or course.teacher.username) if course.teacher_id else '')}</td></tr>"
            f"{rows}</table>")


def _stamp(status):
    if status == Status.APPROVED:
        return "<div class='stamp'>تأیید شده</div>"
    if status == Status.DRAFT:
        return "<div class='stamp'>پیش‌نویس</div>"
    return ""


def _annual(plan):
    course = plan.course
    per_week, total = plan_totals(plan)
    rows = []
    for r in plan.rows.all():
        d = r.date
        month = system_date(d).split(" ")[1] if d else ""
        rows.append(f"<tr><td class='c'>{e(month)}</td><td class='c'>{e(r.index)}</td>"
                    f"<td class='c'>{e(['دوشنبه','سه‌شنبه','چهارشنبه','پنج‌شنبه','جمعه','شنبه','یکشنبه'][d.weekday()] if d else '')}</td>"
                    f"<td class='c'>{e(system_date(d) if d else '')}</td>"
                    f"<td class='c'>{e(r.from_page)}</td><td class='c'>{e(r.to_page)}</td><td>{e(r.topics)}</td></tr>")
    body = "".join(rows) or "<tr><td colspan='7' class='c'>ردیفی ثبت نشده است.</td></tr>"
    head = _head("طرح درس سالانه (جدول زمان‌بندی تدریس) — برگ ۶۰۱", course,
                 [("ساعت تدریس در هفته", per_week), ("تعداد کل جلسات سال", total)])
    return (f"{_stamp(plan.status)}{head}</div><table style='margin-top:6px'><thead><tr><th>ماه</th><th>جلسه</th><th>روز</th>"
            f"<th>تاریخ</th><th>از صفحه</th><th>تا صفحه</th><th>عناوین مورد تدریس</th></tr></thead><tbody>{body}</tbody></table>")


def _daily(d):
    course = d.course
    x = d.data or {}
    obj_rows = "".join(
        f"<tr><td class='c'>{e(o.get('level'))}</td><td>{e(o.get('objective'))}</td>"
        f"<td class='c'>{e(o.get('elements'))}</td><td class='c'>{e(o.get('sphere'))}</td></tr>"
        for o in (x.get("objectives") or [])) or "<tr><td colspan='4' class='c'>—</td></tr>"
    ic = x.get("initial_contact") or {}
    head = _head("طرح درس روزانه — برگ ۶۰۲", course, [
        ("شماره طرح درس", d.number), ("تاریخ اجرا", system_date(d.date)),
        ("مدت اجرا (دقیقه)", d.duration_minutes), ("مکان", d.place),
        ("موضوع درس", d.title), ("تعداد فراگیران", x.get("learners_count", ""))])
    return (f"{_stamp(d.status)}{head}</div>"
            f"<div class='sec'>الف: قبل از تدریس</div>"
            f"<table><tr><td style='width:25%'><b>سطح هدف</b></td><td>{e(x.get('level_goal'))}</td></tr>"
            f"<tr><td><b>هدف کلی</b></td><td>{e(x.get('goal'))}</td></tr>"
            f"<tr><td><b>اهداف مرحله‌ای</b></td><td>{e(x.get('stage_goals'))}</td></tr></table>"
            f"<div class='sec'>هدف‌های رفتاری آموزشی (با رعایت توالی محتوای درسی)</div>"
            f"<table><thead><tr><th>سطح در بلوم</th><th>انتظارات در پایان آموزش</th><th>عناصر (تعقل، ایمان، علم، عمل، اخلاق)</th><th>عرصه ارتباط (خود، خدا، خلق، خلقت)</th></tr></thead>"
            f"<tbody>{obj_rows}</tbody></table>"
            f"<table style='margin-top:4px'>"
            f"<tr><td style='width:25%'><b>رئوس مطالب</b></td><td>{e(x.get('topics'))}</td></tr>"
            f"<tr><td><b>مواد و رسانه‌های آموزشی</b></td><td>{e(x.get('materials'))}</td></tr>"
            f"<tr><td><b>پیش‌بینی رفتار ورودی</b></td><td>{e(x.get('entry_behavior'))}</td></tr>"
            f"<tr><td><b>ایجاد ارتباط اولیه</b></td><td>{e(ic.get('text'))} — <b>زمان:</b> {e(ic.get('minutes'))} دقیقه</td></tr>"
            f"<tr><td><b>گروه‌بندی، مدل و ساختار کلاسی</b></td><td>{e(x.get('grouping'))}</td></tr>"
            f"<tr><td><b>روش ایجاد و تداوم انگیزش</b></td><td>{e(x.get('motivation'))}</td></tr>"
            f"<tr><td><b>ارزشیابی آغازین</b></td><td>{e(x.get('initial_assessment'))}</td></tr>"
            f"<tr><td><b>روش‌های تدریس</b></td><td>{e(x.get('method'))}</td></tr></table>"
            "<div class='foot'><div>دبیر<div class='line'></div></div><div>معاون آموزشی<div class='line'></div></div><div>مدیر<div class='line'></div></div></div>")


def _summary(s):
    course = s.course
    head = _head("ثبت خلاصه فعالیت‌های انجام‌شده در هر جلسه — برگ ۶۰۹", course, [])
    row = (f"<tr><td class='c'>{e(s.session_no)}</td><td class='c'>{e(system_date(s.date))}</td>"
           f"<td>{e(s.activity)}</td><td>{e(s.homework)}</td><td class='c'></td></tr>")
    return (f"{_stamp(s.status)}{head}</div><table style='margin-top:6px'><thead><tr><th>جلسه</th><th>روز و تاریخ</th>"
            f"<th>فعالیت انجام‌شده</th><th>تکلیف محوله به دانش‌آموزان</th><th>امضا</th></tr></thead><tbody>{row}</tbody></table>")


def render(kind, obj):
    if kind == "annual":
        ensure_rows(obj)
        body = _annual(obj)
    elif kind == "daily":
        body = _daily(obj)
    else:
        body = _summary(obj)
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>چاپ کاربرگ</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def render_606(course, term):
    from .gradesheets import sheet_606
    term_label = term if isinstance(term, str) else ("دوم" if 11 in term else "اول")
    d = sheet_606(course, term)
    head = _head("کاربرگ ثبت نمرات مستمر — برگ ۶۰۶", course, [("نیم‌سال", term_label)])
    gl = d["groups"]

    def cell(items):
        return "<br>".join(f"{e(t)}: {e(s)}/{e(m)}" for t, s, m in items) or "—"
    body = []
    for i, r in enumerate(d["rows"], start=1):
        cols = "".join(f"<td style='font-size:10px'>{cell(r['items'][k])}<br><b>جمع: {e(r['sums'][k])}</b></td>" for k in gl)
        body.append(f"<tr><td class='c'>{i}</td><td>{e(r['name'])}</td>{cols}"
                    f"<td class='c'>{e(r['grace'] or '')}</td><td class='c'>{e(r.get('continuous') if r.get('continuous') is not None else '—')}</td>"
                    f"<td class='c'>{e(r.get('final') if r.get('final') is not None else '—')}</td>"
                    f"<td class='c'><b>{e(r.get('term_rule') if r.get('term_rule') is not None else '—')}</b></td></tr>")
    rows = "".join(body) or "<tr><td colspan='7' class='c'>دانش‌آموزی در این درس نیست.</td></tr>"
    heads = "".join(f"<th>{e(v)}</th>" for v in gl.values())
    inner = (f"<table style='margin-top:6px'><thead><tr><th>ردیف</th><th>نام خانوادگی و نام</th>{heads}"
             f"<th>ارفاق</th><th>مستمر</th><th>پایانی</th><th>نمره‌ی نیم‌سال (طبق قاعده)</th></tr></thead><tbody>{rows}</tbody></table>")
    return _doc(f"{head}</div>{inner}")


def render_608(course, term):
    from .gradesheets import sheet_608
    d = sheet_608(course, term)
    head = _head("کاربرگ ثبت فعالیت کلاسی — برگ ۶۰۸", course, [("نیم‌سال", term)])
    dates = d["dates"]
    th = "".join(f"<th>{e(system_date(x))}</th>" for x in dates) or "<th>جلسه‌ای ثبت نشده</th>"
    th += "<th>جمع کسر انضباطی</th>"
    body = []
    for i, r in enumerate(d["rows"], start=1):
        cells = []
        for c in r["cells"]:
            perf = "" if c["perf"] is None else f"<br>{round(c['perf'], 1)}"
            dis = f"<br>کسر انضباطی: {c['dis']}" if c["dis"] else ""
            cells.append(f"<td class='c'>{e(c['mark'])}{e(perf)}{e(dis)}</td>")
        body.append(f"<tr><td class='c'>{i}</td><td>{e(r['name'])}</td>{''.join(cells)}<td class='c'><b>{e(r['dis_total'] or '')}</b></td></tr>")
    rows = "".join(body) or "<tr><td class='c'>—</td></tr>"
    note = ("<div style='font-size:10px;margin-top:4px'>علامت‌ها: ✓ حاضر، غ غایب، ت تأخیر، م موجه، ر مرخصی. "
            "عدد زیر علامت، عملکرد کلاسی آن جلسه است؛ «کسر انضباطی» امتیاز کسرشده‌ی همان روز و ستون آخر جمع کسرهای نیم‌سال است.</div>")
    inner = (f"<table style='margin-top:6px'><thead><tr><th>ردیف</th><th>نام خانوادگی و نام</th>{th}</tr></thead>"
             f"<tbody>{rows}</tbody></table>{note}")
    return _doc(f"{head}</div>{inner}")


def _doc(body):
    return (f"<!doctype html><html dir='rtl'><head><meta charset='utf-8'><title>کاربرگ</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def render_disc(student, term=None):
    from apps.discipline.teacher_rules import mark_printed
    mark_printed(student)
    from .discipline_report import build
    d = build(student, term)
    period = f"نیم‌سال {d['term']}" if d["term"] else "کل سال"
    name = student.get_full_name() or student.username
    head = (f"<div class='form'><div class='ttl'>کارنامه‌ی انضباطی دانش‌آموز</div>"
            f"<table class='kv'><tr><td><b>نام و نام خانوادگی:</b> {e(name)}</td><td><b>کد دانش‌آموزی:</b> {e(student.student_code or '')}</td></tr>"
            f"<tr><td><b>پایه‌ی امتیاز انضباطی:</b> {e(d['base'])}</td><td><b>امتیاز باقی‌مانده:</b> <b>{e(d['remaining'])}</b></td></tr>"
            f"<tr><td><b>دوره:</b> {e(period)}</td><td><b>جمع کسرها:</b> {e(d['total'])}</td></tr></table></div>")
    body = "".join(
        f"<tr><td class='c'>{i}</td><td class='c'>{e(r['date'])}</td><td>{e(r['title'])}</td>"
        f"<td class='c'>{e(r['points'])}</td><td>{e(r['school'])}</td><td>{e(r['note'])}</td></tr>"
        for i, r in enumerate(d["rows"], start=1)) or "<tr><td colspan='6' class='c'>موردی ثبت نشده است.</td></tr>"
    table = (f"<table style='margin-top:6px'><thead><tr><th>ردیف</th><th>تاریخ</th><th>شرح مورد</th>"
             f"<th>کسر</th><th>مدرسه</th><th>توضیح</th></tr></thead><tbody>{body}</tbody></table>")
    return _doc(head + table)

