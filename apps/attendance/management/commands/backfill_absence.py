"""محاسبه‌ی ساعت و دقیقه‌ی غیبت‌های قبلی از روی زنگ‌ها.
غیبت‌هایی که زنگ (شماره‌ی زنگ) دارند و زنگشان در مدرسه تعریف شده، محاسبه می‌شوند.
غیبت‌های بدون زنگ یا با زنگ تعریف‌نشده حذف نمی‌شوند؛ گزارش می‌شوند تا تصمیم با مدیر باشد.
اجرا: python manage.py backfill_absence        (پیش‌نمایش)
      python manage.py backfill_absence --apply  (ذخیره)"""
from django.core.management.base import BaseCommand

from apps.attendance.models import AttendanceRecord
from apps.timetable.models import BellPeriod


class Command(BaseCommand):
    help = "ساعت و دقیقه‌ی غیبت‌های قبلی را از روی زنگ‌ها محاسبه می‌کند."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="ذخیره‌ی تغییرات (بدون این گزینه فقط پیش‌نمایش است).")

    def handle(self, *args, **opts):
        bells = {(b.school_id, b.number): b for b in BellPeriod.objects.all()}
        todo = AttendanceRecord.objects.filter(status=AttendanceRecord.Status.ABSENT, absence_hours=0).select_related("classroom")
        done = missing = nobell = 0
        for r in todo.iterator():
            if not r.period:
                nobell += 1
                continue
            bell = bells.get((r.classroom.school_id, r.period))
            if bell is None:
                missing += 1
                continue
            if opts["apply"]:
                AttendanceRecord.objects.filter(pk=r.pk).update(absence_minutes=bell.minutes, absence_hours=bell.hours)
            done += 1
        mode = "ذخیره شد" if opts["apply"] else "پیش‌نمایش (چیزی ذخیره نشد)"
        self.stdout.write(f"{mode}: محاسبه‌شده={done}، بدون زنگ مشخص={nobell}، زنگ تعریف‌نشده={missing}.")
        self.stdout.write("غیبت‌های بدون زنگ یا با زنگ تعریف‌نشده حذف نشدند؛ در صورت نیاز جداگانه بررسی کنید.")
