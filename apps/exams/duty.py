"""برنامه‌ی مراقبت از امتحانات: هر روز امتحان به تعداد مراقب مورد نیاز، با تنظیم ساعت تدریس هر معلم
(به ازای هر N ساعت تدریس یک روز مراقبت) و با حضور حداقل یک معلم درس آن روز."""
from collections import defaultdict
from decimal import Decimal

from apps.academics.models import GradeWeeklyHours
from apps.courses.models import Course

from .models import ExamDuty, ExamScheduleEntry


def teacher_hours(school):
    """ساعت هفتگی هر معلم در مدرسه: از واحد (مصوبه‌ی پایه و رشته) یا ساعت پیش‌فرض درس."""
    hours = defaultdict(int)
    courses = Course.objects.filter(org=school, teacher__isnull=False, subject__isnull=False, classroom__isnull=False) \
        .select_related("classroom__grade", "classroom__study_field", "subject")
    for c in courses:
        cls = c.classroom
        gw = GradeWeeklyHours.objects.filter(grade_id=cls.grade_id, subject=c.subject, field_id=cls.study_field_id).first() \
            or GradeWeeklyHours.objects.filter(grade_id=cls.grade_id, subject=c.subject, field__isnull=True).first()
        hours[c.teacher_id] += gw.hours if gw else (c.subject.weekly_hours or 1)
    return hours


def generate(schedule, per_day=None):
    """برنامه‌ی مراقبت را از نو می‌سازد: برای هر روز امتحان، معلم درس آن روز (حداقل یکی)، و بقیه بر اساس سهمیه‌ی کمتر."""
    per_day = per_day or schedule.invigilators_per_day or 2
    per_duty_hours = max(1, schedule.hours_per_duty or 6)
    hours = teacher_hours(schedule.school)
    quota = {t: max(1, int(Decimal(h) / Decimal(per_duty_hours) + Decimal("0.5"))) for t, h in hours.items()}
    used = defaultdict(int)
    ExamDuty.objects.filter(schedule=schedule).delete()
    entries = ExamScheduleEntry.objects.filter(schedule=schedule).select_related("classroom", "subject")
    days = sorted({e.date for e in entries})
    made, warnings = 0, []
    for day in days:
        day_entries = [e for e in entries if e.date == day]
        subject_teachers = []
        for e in day_entries:
            c = Course.objects.filter(classroom=e.classroom, subject=e.subject, teacher__isnull=False).first()
            if c is not None and c.teacher_id not in subject_teachers:
                subject_teachers.append(c.teacher_id)
        chosen = []
        if subject_teachers:
            pick = min(subject_teachers, key=lambda t: used[t] / max(quota.get(t, 1), 1))
            chosen.append((pick, True))
        pool = sorted((t for t in hours if t not in [x for x, _ in chosen]),
                      key=lambda t: (used[t] / max(quota.get(t, 1), 1), t))
        for t in pool:
            if len(chosen) >= per_day:
                break
            chosen.append((t, t in subject_teachers))
        if len(chosen) < per_day:
            warnings.append(f"در {day.isoformat()} فقط {len(chosen)} مراقب پیدا شد؛ ساعت تدریس معلمان یا تعداد مراقب مورد نیاز را بررسی کنید.")
        for t, by_sub in chosen:
            ExamDuty.objects.create(schedule=schedule, date=day, teacher_id=t, by_subject=by_sub)
            used[t] += 1
            made += 1
    return made, warnings
