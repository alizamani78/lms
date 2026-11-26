"""قواعد ثبت عامل انضباطی توسط دبیر:
- فقط دانش‌آموزان کلاس‌هایی که آن دبیر در آن‌ها درس دارد؛
- فقط روزهایی که دبیر در برنامه‌ی ساعت حضور خود دارد (اگر ساعت حضور تعریف کرده باشد)."""
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.courses.models import Course
from apps.timetable.models import TeacherAvailability


def teaches_student(teacher, student):
    return Course.objects.filter(teacher=teacher, classroom__students=student).exists()


def present_on(teacher, day):
    """روز میلادی day، روز حضور دبیر است؟ (شنبه=۰ … جمعه=۶ در برنامه؛ اگر ساعت حضور تعریف نشده باشد، آزاد است)."""
    days = set(TeacherAvailability.objects.filter(teacher=teacher).values_list("weekday", flat=True))
    if not days:
        return True
    return ((day.weekday() + 2) % 7) in days


def check_teacher_record(teacher, student, day):
    if not teaches_student(teacher, student):
        raise PermissionDenied("دبیر فقط برای دانش‌آموزان کلاس‌های خودش عامل ثبت می‌کند.")
    if not present_on(teacher, day):
        raise ValidationError({"date": "این تاریخ در ساعت حضور شما در مدرسه نیست؛ فقط روزهای حضورتان قابل ثبت است."})


def can_teacher_change(teacher, record):
    """دبیر می‌تواند عامل دانش‌آموزان کلاس خودش را تا قبل از چاپ کارنامه‌ی انضباطی ویرایش یا حذف کند."""
    return record.printed_at is None and teaches_student(teacher, record.student)


def mark_printed(student):
    """با چاپ کارنامه‌ی انضباطی، عامل‌های آن دانش‌آموز قفل می‌شوند (دیگر دبیر نمی‌تواند تغییر دهد)."""
    from django.utils import timezone
    from apps.discipline.models import DisciplineRecord
    DisciplineRecord.objects.filter(student=student, printed_at__isnull=True).update(printed_at=timezone.now())
