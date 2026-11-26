from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import Branch, Level

# Branches per level. Secondary (متوسطه دوم) has the vocational branches; primary and lower secondary are general.
SECONDARY_BRANCHES = ["نظری", "کاردانش", "فنی و حرفه‌ای", "فنی حرفه‌ای و کاردانش", "حرفه‌ای استثنایی"]


class Command(BaseCommand):
    help = "Create the branches (شاخه) of every level: general for all levels, and the secondary branches for متوسطه دوم."

    @transaction.atomic
    def handle(self, *args, **opts):
        made = 0
        for level in Level.objects.all():
            names = ["عمومی"]
            if "دوم" in level.name and "متوسطه" in level.name:
                names += SECONDARY_BRANCHES
            for name in names:
                _, created = Branch.objects.get_or_create(level=level, name=name)
                made += int(created)
        self.stdout.write(self.style.SUCCESS(f"{made} شاخه ثبت شد."))
