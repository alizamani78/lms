"""برگ ۶۰۶ (ثبت نمرات مستمر) و برگ ۶۰۸ (ثبت فعالیت کلاسی) از داده‌های واقعی سامانه.

منبع‌ها:
  حضور و غیاب ← attendance.AttendanceRecord (همان درس، تاریخ جلسه)
  نمره‌ی سرکلاسی ← evaluation.ClassGrade با دوره‌ی class (هر نمره با تاریخ جلسه)
  ارفاق ← evaluation.GraceAward
  انضباطی ← discipline.DisciplineRecord (تعداد موارد هر دانش‌آموز در همان تاریخ)
  آزمون‌ها ← نمره‌ی مستمر کل از evaluation.classgrades (درصد)
نیم‌سال: اول = مهر تا بهمن (ماه ۷ تا ۱۰ شمسی)، دوم = بهمن تا تیر سال بعد (ماه ۱۱ تا ۶). این بازه با TERM_MONTHS قابل تغییر است.
"""
from collections import defaultdict

from apps.academics.models import ClassRoom
from apps.attendance.models import AttendanceRecord
from apps.core.jalali import gregorian_to_jalali
from apps.courses.roster import roster_ids
from apps.discipline.models import DisciplineRecord
from apps.evaluation.models import ClassGrade, GraceAward

TERM_MONTHS = {"اول": (7, 8, 9, 10), "دوم": (11, 12, 1, 2, 3, 4, 5, 6)}
STATUS_MARK = {"present": "✓", "absent": "غ", "late": "ت", "excused": "م", "leave": "ر"}


def _in_term(d, term):
    return gregorian_to_jalali(d.year, d.month, d.day)[1] in TERM_MONTHS.get(term, TERM_MONTHS["اول"])


def _students(course):
    from apps.accounts.models import User
    return list(User.objects.filter(pk__in=roster_ids(course)).order_by("last_name", "first_name", "id"))


def sheet_608(course, term):
    """ستون‌ها: جلسه‌های درس (تاریخ‌های حضور و غیاب در این نیم‌سال). ردیف‌ها: دانش‌آموزان."""
    from .grade_rules import TERM_MONTHS
    if isinstance(term, str):
        term = TERM_MONTHS.get(term, TERM_MONTHS["اول"])
    term = list(term)
    students = _students(course)
    ids = [s.pk for s in students]
    recs = AttendanceRecord.objects.filter(course=course, student_id__in=ids)
    dates = sorted({r.date for r in recs if _in_term(r.date, term)})
    st = {(r.student_id, r.date): r for r in recs}
    cg = defaultdict(list)
    for g in ClassGrade.objects.filter(course=course, student_id__in=ids, period="class"):
        if g.date:
            cg[(g.student_id, g.date)].append(float(g.score))
    dis = defaultdict(float)          # کسر امتیاز انضباطی (مقدار مثبت = کسر)
    dis_total = defaultdict(float)
    for d in DisciplineRecord.objects.filter(student_id__in=ids, date__in=dates):
        dis[(d.student_id, d.date)] += abs(float(d.points))
        dis_total[d.student_id] += abs(float(d.points))
    rows = []
    for s in students:
        cells = []
        for d in dates:
            r = st.get((s.pk, d))
            mark = STATUS_MARK.get(r.status, "") if r else ""
            perf = cg.get((s.pk, d))
            cells.append({"mark": mark, "perf": (sum(perf) / len(perf)) if perf else None, "dis": round(dis.get((s.pk, d), 0), 2)})
        rows.append({"name": s.get_full_name() or s.username, "cells": cells, "dis_total": round(dis_total.get(s.pk, 0), 2)})
    return {"dates": dates, "rows": rows}


def sheet_606(course, term):
    """نمره‌ی مستمر به تفکیک: سرکلاسی، ماهانه، آزمون‌ها (میان‌ترم و پایان ترم)، با همه‌ی موارد (بدون محدودیت تعداد)."""
    from .score_engine import course_term_grade
    from .grade_rules import TERM_MONTHS
    # the printing passes the term's name ("اول"/"دوم"); the months are needed for the marks
    if isinstance(term, str):
        term = list(TERM_MONTHS.get(term, TERM_MONTHS["اول"]))
    # the term may come as its name ("اول"/"دوم") from the print link; the months are used from here on
    if isinstance(term, str):
        term = TERM_MONTHS.get(term, TERM_MONTHS["اول"])
    term = list(term)
    term_name = "دوم" if set(term) == set(TERM_MONTHS["دوم"]) else "اول"
    students = _students(course)
    ids = [s.pk for s in students]
    groups = {"class": "سرکلاسی", "monthly": "ماهانه", "exam": "آزمون"}
    buckets = defaultdict(lambda: defaultdict(list))
    for g in ClassGrade.objects.filter(course=course, student_id__in=ids).order_by("date", "id"):
        if g.date is not None and not _in_term(g.date, term):
            continue
        key = "exam" if g.period in ("midterm", "final") else g.period
        if key in groups:
            buckets[g.student_id][key].append(g)
    grace = defaultdict(float)
    for a in GraceAward.objects.filter(course=course, student_id__in=ids):
        grace[a.student_id] += float(a.points)
    rows = []
    for s in students:
        b = buckets.get(s.pk, {})
        sums = {}
        items = {}
        for k in groups:
            gl = b.get(k, []) if isinstance(b, dict) else []
            items[k] = [(g.title, float(g.score), float(g.max_score)) for g in gl]
            sums[k] = round(sum(float(g.score) for g in gl), 2)
        total = round(sum(sums.values()) + grace.get(s.pk, 0), 2)
        # the term mark by the confirmed rule: (مستمر × ۱ + پایانی × ۲) ÷ ۳ (score_engine)
        eng = course_term_grade(course, s.pk, term_name, term)
        rows.append({"name": s.get_full_name() or s.username, "items": items, "sums": sums,
                     "grace": grace.get(s.pk, 0), "total": total,
                     "continuous": float(eng["continuous"]) if eng["continuous"] is not None else None,
                     "final": float(eng["final"]) if eng["final"] is not None else None,
                     "term_rule": float(eng["term"]) if eng["term"] is not None else None})
    return {"groups": groups, "rows": rows}


def classroom_of(course):
    return course.classroom if isinstance(course.classroom, ClassRoom) else None
