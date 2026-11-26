"""ترکیب نمره‌ی دستی و آزمون مجازی یک امتحان، و ثبت نمره‌ی نهایی در نمره‌ی پایانی درس."""
from decimal import Decimal

from apps.evaluation.models import ClassGrade


def online20(exam, student_id):
    """نمره‌ی آزمون مجازی دانش‌آموز از ۲۰ (آخرین تلاش ثبت‌شده)؛ اگر نبود، None."""
    from .models import Attempt
    att = Attempt.objects.filter(exam=exam, student_id=student_id, submitted_at__isnull=False).order_by("-submitted_at", "-id").first()
    if att is None or not att.total:
        return None
    return (Decimal(att.score) / Decimal(att.total) * 20).quantize(Decimal("0.01"))


def combine(mode, manual, online):
    """ترکیب طبق ترکیب آزمون؛ اگر یکی از نمره‌ها نباشد، همان یکی به کار می‌رود."""
    if mode == "manual":
        return manual
    if mode == "online":
        return online
    values = [v for v in (manual, online) if v is not None]
    if not values:
        return None
    if mode == "max":
        return max(values)
    if mode == "min":
        return min(values)
    return (sum(values) / Decimal(len(values))).quantize(Decimal("0.01"))


def apply_exam(exam, students):
    """نمره‌ی نهایی هر دانش‌آموز را طبق ترکیب آزمون در نمره‌ی پایانی درس می‌نویسد (یا پاک می‌کند اگر نمره‌ای نباشد)."""
    from .models import ExamManualScore
    course = exam.course
    manual = {m.student_id: m.score for m in ExamManualScore.objects.filter(exam=exam)}
    from .score_review import is_locked
    for sid in students:
        if is_locked(exam, sid):
            continue
        final = combine(exam.combine_mode, manual.get(sid), online20(exam, sid))
        qs = ClassGrade.objects.filter(course=course, student_id=sid, title=exam.title, period="final")
        if final is None:
            qs.delete()
            continue
        ClassGrade.objects.update_or_create(course=course, student_id=sid, title=exam.title, period="final",
                                            defaults={"score": final, "max_score": 20, "date": exam.starts_at.date() if exam.starts_at else None,
                                                      "recorded_by": exam.course.teacher})
