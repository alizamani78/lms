from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.jalali import jalali_to_gregorian
from apps.schoolcal.models import Occasion
from apps.schoolcal.occasions_data import OCCASIONS


class Command(BaseCommand):
    help = "Load the official holidays and occasions of 1405 and 1406 (idempotent; existing rows are updated)."

    @transaction.atomic
    def handle(self, *args, **opts):
        made = updated = 0
        for jy, jm, jd, title, cat, is_hol, note in OCCASIONS:
            gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
            obj, created = Occasion.objects.update_or_create(
                jalali_year=jy, jalali_month=jm, jalali_day=jd, title=title,
                defaults={"gregorian_date": date(gy, gm, gd), "category": cat, "is_holiday": is_hol,
                          "description": note, "is_active": True})
            made += int(created)
            updated += int(not created)
        self.stdout.write(self.style.SUCCESS(f"{made} مناسبت ثبت شد، {updated} مورد به‌روز شد."))
