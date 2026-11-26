"""کارنامه‌ی انضباطی دانش‌آموز: همه‌ی موارد ثبت‌شده، کسرها، و امتیاز باقی‌مانده.

کسرها از رکوردهای انضباطی خوانده می‌شوند (مقدار مطلق امتیاز = کسر). پایه‌ی امتیاز انضباطی
DISCIPLINE_BASE است (پیش‌فرض ۲۰)؛ امتیاز باقی‌مانده = پایه − جمع کسرها، و نمی‌تواند منفی شود.
"""
from decimal import Decimal

from apps.core.jalali import system_date
from apps.discipline.models import DisciplineRecord

DISCIPLINE_BASE = Decimal("20")


def build(student, term=None):
    """term = «اول» یا «دوم» برای فیلتر نیم‌سال؛ None یعنی همه‌ی موارد سال."""
    from .gradesheets import _in_term
    recs = list(DisciplineRecord.objects.filter(student=student).order_by("date", "id"))
    if term:
        recs = [r for r in recs if _in_term(r.date, term)]
    rows = []
    total = Decimal("0")
    for r in recs:
        pts = abs(Decimal(r.points))
        total += pts
        rows.append({"date": system_date(r.date), "title": r.title,
                     "points": pts, "note": r.note or "", "school": r.school.name})
    remaining = max(Decimal("0"), DISCIPLINE_BASE - total)
    return {"rows": rows, "total": total, "base": DISCIPLINE_BASE, "remaining": remaining, "term": term}
