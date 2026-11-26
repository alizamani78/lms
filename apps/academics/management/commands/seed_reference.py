"""Load the standard Iranian school levels, grades, study fields and the current academic year.

    py manage.py seed_reference

Idempotent. Subjects (curriculum) are not seeded: add them in the admin or import them from the official list.
"""
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import AcademicYear, Grade, Level, StudyField

LEVELS = [
    ("ابتدایی", ["اول", "دوم", "سوم", "چهارم", "پنجم", "ششم"]),
    ("متوسطه اول", ["هفتم", "هشتم", "نهم"]),
    ("متوسطه دوم", ["دهم", "یازدهم", "دوازدهم"]),
]
FIELDS = [
    ("ریاضی و فیزیک", "متوسطه دوم"), ("علوم تجربی", "متوسطه دوم"),
    ("علوم و معارف اسلامی", "متوسطه دوم"), ("ادبیات و علوم انسانی", "متوسطه دوم"),
    ("فنی و حرفه‌ای", "متوسطه دوم"), ("کار و دانش", "متوسطه دوم"),
]


class Command(BaseCommand):
    help = "Seed levels, grades, study fields and the current academic year."

    @transaction.atomic
    def handle(self, *args, **opts):
        levels = {}
        n = 0
        for li, (lname, grades) in enumerate(LEVELS, 1):
            level, _ = Level.objects.get_or_create(name=lname, defaults={"order": li})
            levels[lname] = level
            for gi, gname in enumerate(grades, 1):
                _, created = Grade.objects.get_or_create(level=level, name=gname, defaults={"order": gi})
                n += created
        for fname, lname in FIELDS:
            StudyField.objects.get_or_create(name=fname, defaults={"level": levels[lname]})
        # ۱۴۰۵-۱۴۰۶: approximate Gregorian dates; adjust in the admin to the official calendar.
        if not AcademicYear.objects.exists():
            AcademicYear.objects.create(name="۱۴۰۵-۱۴۰۶", starts_on=date(2026, 9, 23),
                                        ends_on=date(2027, 6, 22), is_current=True)
        self.stdout.write(self.style.SUCCESS(f"Reference data ready ({n} new grades)."))
