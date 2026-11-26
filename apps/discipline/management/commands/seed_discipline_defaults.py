from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.discipline.defaults import DEFAULT_FACTORS
from apps.discipline.models import DisciplineFactor


class Command(BaseCommand):
    help = "Load the default disciplinary factors (shared catalogue). Existing ones are kept."

    @transaction.atomic
    def handle(self, *args, **opts):
        made = 0
        for title, pts in DEFAULT_FACTORS:
            _, created = DisciplineFactor.objects.get_or_create(school=None, title=title, defaults={"points": Decimal(pts)})
            made += int(created)
        self.stdout.write(self.style.SUCCESS(f"{made} عامل پیش‌فرض اضافه شد."))
