from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import Level, StudyField
from apps.core.persian import normalize_text

# these were stored as levels by mistake; they are branches (شاخه) of متوسطه دوم
BRANCH_NAMES = ["کاردانش", "فنی و حرفه‌ای", "فنی حرفه‌ای و کاردانش", "حرفه‌ای استثنایی", "نظری", "عمومی"]


class Command(BaseCommand):
    help = "Remove the branch names that were stored as levels (کاردانش، فنی و حرفه‌ای …) when nothing uses them."

    @transaction.atomic
    def handle(self, *args, **opts):
        wanted = {normalize_text(x) for x in BRANCH_NAMES}
        removed, kept = 0, []
        for lv in Level.objects.all():
            if normalize_text(lv.name) not in wanted:
                continue
            if lv.schools.exists() or lv.grades.exists() or StudyField.objects.filter(level=lv).exists():
                kept.append(lv.name)
                continue
            lv.delete()
            removed += 1
        self.stdout.write(self.style.SUCCESS(f"{removed} مقطع اشتباه حذف شد."))
        if kept:
            self.stdout.write(self.style.WARNING("این موارد هنوز در استفاده‌اند و حذف نشدند: " + "، ".join(kept) + ". مدرسه‌ها را به مقطع درست منتقل کنید و دوباره اجرا کنید."))
