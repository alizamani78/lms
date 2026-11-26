"""موتور نمره‌ی نیم‌سال، مطابق قواعد تأییدشده:
- ماهیانه (هر ماه): نمره‌ی معلم یا میانگین سرکلاسی از اول نیم‌سال تا تاریخ ثبت (grade_rules).
- مستمر نیم‌سال: میانگین ماهیانه‌ها؛ اگر آزمون مستمر (assessment = continuous) باشد، ترکیب با درصدهای آزمون
  (monthly_percent؛ خالی = برابر).
- پایانی نیم‌سال: نمره‌ی آزمون پایانی (assessment = final)، ضریب ۲.
- نمره‌ی نیم‌سال = (مستمر × ۱ + پایانی × ۲) ÷ ۳.
این موتور نمره‌ی هر درس را برای هر دانش‌آموز می‌دهد؛ کارنامه و دفتر نمرات از همین استفاده می‌کنند."""
from decimal import Decimal

from apps.evaluation.models import ClassGrade

from .grade_rules import continuous, monthly_average, monthly_value, q2, term_grade
from .monthly import MONTH_NAMES, _month_start_end


def _exam_scores(course, student_ids, assessment):
    """نمره‌ی آزمون‌های یک نوع (از ۲۰) برای هر دانش‌آموز: {(sid): (نمره, درصد ماهیانه)}."""
    from apps.exams.models import Exam
    out = {}
    for ex in Exam.objects.filter(course=course, assessment=assessment):
        for g in ClassGrade.objects.filter(course=course, title=ex.title, period="final", student_id__in=student_ids):
            out.setdefault(g.student_id, (Decimal(g.score), ex.monthly_percent))
    return out


def course_term_grade(course, student_id, term, months):
    """نمره‌ی نیم‌سال یک درس برای یک دانش‌آموز؛ و جزئیات (مستمر، پایانی، ماهیانه‌ها)."""
    from apps.accounts.models import User
    if isinstance(months, str):
        from .grade_rules import TERM_MONTHS
        months = list(TERM_MONTHS.get(months, TERM_MONTHS["اول"]))
    student = User.objects.get(pk=student_id)
    monthly = {}
    for m in months:
        start, end = _month_start_end(term, m)
        teacher = ClassGrade.objects.filter(course=course, student=student, period="monthly", title=f"ماهانه {MONTH_NAMES[m]}").first()
        cls = [(g.date, g.score, g.max_score) for g in ClassGrade.objects.filter(course=course, student=student, period="class", date__gte=start, date__lte=end)]
        v = monthly_value(monthly_average(cls, term, end), teacher.score if teacher else None)
        if v is not None:
            monthly[m] = Decimal(v)
    monthly_mean = q2(sum(monthly.values()) / len(monthly)) if monthly else None
    cont_exam = _exam_scores(course, [student_id], "continuous").get(student_id)
    if cont_exam is None:
        cont = continuous(monthly_mean, None)
    else:
        exam20, pct = cont_exam
        cont = continuous(monthly_mean, exam20, pct)
    fin_exam = _exam_scores(course, [student_id], "final").get(student_id)
    fin = fin_exam[0] if fin_exam else None
    return {"continuous": cont, "final": fin, "term": term_grade(cont, fin), "monthly": monthly}
