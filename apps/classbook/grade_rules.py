"""قواعد محاسبه‌ی نمره‌ها (تأییدشده):
- نمره‌ی ماهیانه: میانگین نمرات سرکلاسی (از ۲۰) از اول نیم‌سال تا تاریخ ثبت. نیم‌سال اول از اول مهر (ماه ۷)، دوم از اول بهمن (ماه ۱۱).
- مستمر ۱ / مستمر ۲: میانگین نمرات ماهیانه (به تنهایی، یا به همراه آزمون مستمر)، ضریب ۱.
- پایانی ۱ / پایانی ۲: نمره‌ی دستی یا مجازی، ضریب ۲.
- نمره‌ی هر نوبت = (مستمر × ۱ + پایانی × ۲) ÷ ۳.
- نمره‌ی سالانه = (مستمر ۱ × ۱ + پایانی ۱ × ۲ + مستمر ۲ × ۱ + پایانی ۲ × ۴) ÷ ۸.
- معدل = مجموع (نمره × واحد) ÷ مجموع واحدها.
- هشدار: معدل کارنامه‌ی هشدار = مجموع واحدها ÷ (مستمر ۱ × واحد) — به‌صورت نسبت (ادامه‌ی مشخصات در کارنامه)."""
from decimal import ROUND_HALF_UP, Decimal

from apps.core.jalali import gregorian_to_jalali

TERM_MONTHS = {"اول": (7, 8, 9, 10), "دوم": (11, 12, 1, 2, 3, 4, 5, 6)}
TERM_START_MONTH = {"اول": 7, "دوم": 11}   # مهر، بهمن


def q2(x):
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def avg(values):
    vals = [Decimal(v) for v in values if v is not None]
    return q2(sum(vals) / len(vals)) if vals else None


def jmonth(d):
    return gregorian_to_jalali(d.year, d.month, d.day)[1]


def in_term_until(d, term, until):
    """تاریخ d در نیم‌سال از اول نیم‌سال تا تاریخ until باشد."""
    jm = jmonth(d)
    return jm in TERM_MONTHS[term] and d <= until


def monthly_average(class_grades, term, until):
    """میانگین نمرات سرکلاسی (از ۲۰) از اول نیم‌سال تا تاریخ until؛ class_grades: [(تاریخ، نمره، کل)]."""
    scaled = [Decimal(s) * 20 / Decimal(m) for d, s, m in class_grades if in_term_until(d, term, until) and Decimal(m) > 0]
    return avg(scaled)


def monthly_value(computed, teacher_grade=None):
    """نمره‌ی ماهیانه: اگر معلم یک نمره‌ی ماهیانه ثبت کرده باشد، همان؛ وگرنه میانگین محاسبه‌شده."""
    if teacher_grade is not None:
        return q2(teacher_grade)
    return computed


def continuous(monthly, exam20=None, monthly_percent=None):
    """مستمر: ترکیب ماهیانه و آزمون مستمر با درصدهای معلم (مجموع ۱۰۰)؛ خالی یعنی برابر (۵۰ و ۵۰).
    اگر یکی از دو نمره نباشد، همان یکی به کار می‌رود."""
    if monthly_percent is not None and not 0 <= int(monthly_percent) <= 100:
        raise ValueError("درصد باید بین ۰ تا ۱۰۰ باشد.")
    if exam20 is None:
        return q2(monthly) if monthly is not None else None
    if monthly is None:
        return q2(exam20)
    pm = Decimal(50 if monthly_percent is None else int(monthly_percent)) / 100
    return q2(Decimal(monthly) * pm + Decimal(exam20) * (1 - pm))


def term_grade(cont, final):
    """نمره‌ی نوبت = (مستمر × ۱ + پایانی × ۲) ÷ ۳."""
    if cont is None or final is None:
        return None
    return q2((Decimal(cont) * 1 + Decimal(final) * 2) / 3)


def annual_grade(c1, f1, c2, f2):
    """نمره‌ی سالانه = (مستمر۱×۱ + پایانی۱×۲ + مستمر۲×۱ + پایانی۲×۴) ÷ ۸."""
    parts = [(c1, 1), (f1, 2), (c2, 1), (f2, 4)]
    if any(v is None for v, _ in parts):
        return None
    return q2(sum(Decimal(v) * w for v, w in parts) / 8)


def average_by_units(items):
    """معدل = مجموع (نمره × واحد) ÷ مجموع واحدها؛ items: [(نمره، واحد)]."""
    items = [(Decimal(s), Decimal(u)) for s, u in items if s is not None and u]
    total_units = sum(u for _, u in items)
    if not total_units:
        return None
    return q2(sum(s * u for s, u in items) / total_units)


def progress(current_avg, previous_avg):
    """میزان پیشرفت = میانگین ماه جاری − میانگین ماه قبل (عدد و نمودار)."""
    if current_avg is None or previous_avg is None:
        return None
    return q2(Decimal(current_avg) - Decimal(previous_avg))
