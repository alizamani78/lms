"""Create the FIXED solar national holidays of a Jalali year as nationwide, attendance-blocking events.

    py manage.py seed_holidays --year 1405

Lunar-calendar holidays (Eid al-Fitr, Ashura, Tasua, Arbaeen, ...) move every year and are NOT generated here:
enter them from the official calendar in the admin (or via POST /api/calendar/)."""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.jalali import jalali_to_gregorian
from apps.schoolcal.models import CalendarEvent
from datetime import date

FIXED = [
    (1, 1, "عید نوروز (۱ فروردین)"), (1, 2, "عید نوروز (۲ فروردین)"), (1, 3, "عید نوروز (۳ فروردین)"),
    (1, 4, "عید نوروز (۴ فروردین)"), (1, 12, "روز جمهوری اسلامی"), (1, 13, "سیزده‌به‌در"),
    (3, 14, "رحلت امام خمینی (ره)"), (3, 15, "قیام ۱۵ خرداد"), (11, 22, "پیروزی انقلاب اسلامی"),
    (12, 29, "ملی شدن صنعت نفت ایران"),
]


class Command(BaseCommand):
    help = "Seed fixed solar national holidays for a Jalali year (nationwide)."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True, help="Jalali year, e.g. 1405")

    @transaction.atomic
    def handle(self, *args, **opts):
        n = 0
        for jm, jd, title in FIXED:
            d = date(*jalali_to_gregorian(opts["year"], jm, jd))
            _, created = CalendarEvent.objects.get_or_create(
                org=None, kind="holiday", start_date=d, end_date=d,
                defaults={"title": title, "blocks_attendance": True})
            n += created
        self.stdout.write(self.style.SUCCESS(
            f"{n} holiday(s) created. Remember to add the lunar-calendar holidays from the official calendar."))
