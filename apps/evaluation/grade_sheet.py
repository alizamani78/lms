"""برگ ریز نمرات دبیر: چاپی مانند برگ رسمی (سربرگ وزارتخانه و مدرسه، جدول دانش‌آموزان، نمره‌ی مستمر و پایانی به عدد و حروف، امضاها).

نگاشت نمره‌ها (همان منطق کارنامه‌ی سامانه):
  مستمر  = میانگین نمره‌های سرکلاسی و ماهانه همان درس، از ۲۰
  پایانی = نمره‌ی پایان ترم (اگر ثبت شده)، وگرنه درصد کل درس از ۲۰
  ارفاق (اگر ثبت شده) به نمره‌ی کل اضافه و در ملاحظات نوشته می‌شود.
"""
import html
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.accounts.models import SchoolMembership, SchoolProfile, User
from apps.core.jalali import format_jalali
from apps.evaluation.models import ClassGrade, GraceAward

UNITS = ["صفر", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
TEENS = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
TENS = {2: "بیست", 3: "سی", 4: "چهل", 5: "پنجاه", 6: "شصت", 7: "هفتاد", 8: "هشتاد", 9: "نود"}
FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _under100(n):
    if n < 10:
        return UNITS[n]
    if n < 20:
        return TEENS[n - 10]
    t, u = divmod(n, 10)
    return TENS[t] + ("" if u == 0 else "و" + UNITS[u])


def score_words(value):
    """۱۸٫۵ ← «هجدهوپنجاه صدم»؛ ۲۰ ← «بیست»؛ به شیوه‌ی برگ رسمی (بدون فاصله)."""
    if value is None:
        return ""
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole = int(d)
    frac = int((d - whole) * 100)
    text = _under100(whole)
    if frac:
        text += "و" + _under100(frac) + " صدم"
    return text


def fa(x):
    return str(x if x is not None else "").translate(FA_DIGITS)


def _scale20(value_pct):
    return (Decimal(str(value_pct)) * Decimal("0.2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def build_rows(course, classroom):
    from apps.courses.models import Enrollment
    from apps.evaluation.classgrades import course_percentages
    students = list(User.objects.filter(
        pk__in=Enrollment.objects.filter(course=course).values_list("student_id", flat=True), role="student")
        .order_by("last_name", "first_name", "id"))
    ids = [s.pk for s in students]
    pct = course_percentages([course.org_id], student_ids=ids, course_ids=[course.pk])
    cg = {}
    for g in ClassGrade.objects.filter(course=course, student_id__in=ids):
        cg.setdefault((g.student_id, g.period), []).append(float(g.score) / float(g.max_score or 1) * 100)
    grace = {}
    for a in GraceAward.objects.filter(course=course, student_id__in=ids):
        grace[a.student_id] = grace.get(a.student_id, 0) + float(a.points)
    rows = []
    for s in students:
        cont_items = cg.get((s.pk, "class"), []) + cg.get((s.pk, "monthly"), [])
        cont = (sum(cont_items) / len(cont_items)) if cont_items else None
        fin_items = cg.get((s.pk, "final"), [])
        if fin_items:
            final = sum(fin_items) / len(fin_items)
        else:
            rec = pct.get((s.pk, course.pk))
            final = (sum(rec[2] + rec[3]) / len(rec[2] + rec[3])) if rec and (rec[2] + rec[3]) else None
        cont20 = _scale20(cont) if cont is not None else None
        final20 = _scale20(final) if final is not None else None
        notes = f"ارفاق {fa(grace[s.pk])}" if grace.get(s.pk) else ""
        rows.append({"code": s.student_code or "", "name": (s.get_full_name() or s.username),
                     "father": getattr(s, "father_name", "") or "", "cont": cont20, "final": final20, "notes": notes})
    return rows


def _staff(school, roles):
    u = SchoolMembership.objects.filter(school=school, user__role__in=roles).select_related("user").first()
    return (u.user.get_full_name() or u.user.username) if u else ""


def build_grade_sheet(course, classroom, term="اول"):
    school = course.org
    prof = SchoolProfile.objects.filter(school=school).select_related("level").first()
    level = prof.level.name if prof and prof.level_id else ""
    branch = prof.branch if prof and getattr(prof, "branch", "") else ""
    org = school
    region = org.parent.name if org.parent_id and org.parent.kind == "region" else ""
    county = org.parent.name if org.parent_id and org.parent.kind == "county" else (org.parent.parent.name if org.parent_id and org.parent.parent_id else "")
    province = ""
    node = org
    while node is not None and node.kind != "province":
        node = node.parent
    province = node.name if node else ""
    field = classroom.study_field.name if classroom and classroom.study_field_id else ""
    teacher = (course.teacher.get_full_name() or course.teacher.username) if course.teacher_id else ""
    rows = build_rows(course, classroom)
    deputy = _staff(school, ["school_deputy_edu"])
    principal = _staff(school, ["school_admin"])
    return {"school": school.name, "code": prof.school_code if prof else "", "level": level, "branch": branch,
            "field": field, "province": province, "county": county, "region": region, "course": course.title,
            "course_code": (getattr(course.subject, "code", "") or "") if course.subject_id else "", "classroom": classroom.name if classroom else "",
            "teacher": teacher, "deputy": deputy, "principal": principal,
            "year": classroom.academic_year.name if classroom and classroom.academic_year_id else "",
            "term": term, "rows": rows}


CSS = """
@font-face{font-family:"VazirGrade";src:url("/static/fonts/Vazirmatn-Regular.woff2") format("woff2");font-weight:400}
@font-face{font-family:"VazirGrade";src:url("/static/fonts/Vazirmatn-Bold.woff2") format("woff2");font-weight:700 800}
@page{size:A4 portrait;margin:10mm}
html,body{font-family:"VazirGrade",Tahoma,sans-serif;color:#000;font-size:11px;line-height:1.6;margin:0;background:#fff}
.head{display:flex;justify-content:space-between;align-items:flex-start;border:1.5px solid #000;padding:6px 8px}
.head .c{text-align:center;font-weight:800;font-size:13px}
.head .r,.head .l{font-size:11px;line-height:1.9}
.title{text-align:center;font-weight:800;font-size:14px;margin:8px 0 4px}
table{width:100%;border-collapse:collapse;margin-top:4px}
th,td{border:1px solid #000;padding:3px 4px;text-align:center;vertical-align:middle}
th{font-weight:800;background:#f0f0f0}
td.nm,td.fa{text-align:right}
.sign{display:flex;justify-content:space-between;margin-top:18px;font-size:11px}
.sign div{text-align:center;width:31%}
.sign .line{border-bottom:1px dotted #000;height:26px;margin-top:4px}
"""


def render_html(d):
    e = html.escape
    rows_html = []
    for i, r in enumerate(d["rows"], start=1):
        cont = r["cont"]; final = r["final"]
        rows_html.append(
            f"<tr><td>{fa(i)}</td><td>{e(fa(r['code']))}</td><td class='nm'>{e(r['name'])}</td><td class='fa'>{e(r['father'])}</td>"
            f"<td>{e(fa(cont)) if cont is not None else ''}</td><td>{e(score_words(cont))}</td>"
            f"<td>{e(fa(final)) if final is not None else ''}</td><td>{e(score_words(final))}</td><td>{e(r['notes'])}</td></tr>")
    body = "".join(rows_html) or "<tr><td colspan='9'>دانش‌آموزی در این درس ثبت نشده است.</td></tr>"
    return f"""<!doctype html><html dir="rtl"><head><meta charset="utf-8"><title>برگ ریز نمرات</title>
<style>{CSS}</style></head><body>
<div class="head">
  <div class="r">وزارت آموزش و پرورش<br>استان: {e(d['province'])}<br>شهرستان: {e(d['county'])}<br>ناحیه: {e(d['region'])}</div>
  <div class="c">برگ ریز نمرات دبیر<br><span style="font-weight:400">نوبت {e(d['term'])}</span><br>سال تحصیلی {e(d['year'])}</div>
  <div class="l">مدرسه: {e(d['school'])}<br>کد مدرسه: {e(fa(d['code']))}<br>دوره: {e(d['level'])}{(' — ' + e(d['branch'])) if d['branch'] else ''}<br>رشته: {e(d['field'])}</div>
</div>
<div class="head" style="border-top:0">
  <div class="r">درس: {e(d['course'])}<br>کد درس: {e(fa(d['course_code']))}</div>
  <div class="c">کلاس: {e(d['classroom'])}</div>
  <div class="l">نام دبیر: {e(d['teacher'])}</div>
</div>
<table>
<thead><tr><th rowspan="2">ردیف</th><th rowspan="2">کد دانش‌آموز</th><th rowspan="2">نام خانوادگی و نام</th><th rowspan="2">نام پدر</th>
<th colspan="2">نمره‌ی مستمر (از ۲۰)</th><th colspan="2">نمره‌ی پایانی (از ۲۰)</th><th rowspan="2">ملاحظات</th></tr>
<tr><th>عدد</th><th>حروف</th><th>عدد</th><th>حروف</th></tr></thead>
<tbody>{body}</tbody></table>
<div class="sign">
  <div>دبیر: {e(d['teacher'])}<div class="line"></div>تاریخ و امضاء</div>
  <div>معاون آموزشی: {e(d['deputy'])}<div class="line"></div>تاریخ و امضاء</div>
  <div>مدیر: {e(d['principal'])}<div class="line"></div>تاریخ و امضاء و مهر</div>
</div>
<p style="font-size:10px;margin-top:10px">تاریخ چاپ: {e(format_jalali(timezone.now(), with_time=False))}</p>
</body></html>"""
