"""Standard catalogue: levels, grades 1-12 and the theoretical fields of the upper secondary level.
    py manage.py seed_catalog
Idempotent. Every row keeps its code; codes and names can be edited or deleted afterwards in the admin (super admins).
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.academics.models import Grade, Level, StudyField

LEVELS = [  # code, name, grades (code, name)
    ("PRI", "ابتدایی", [(f"G{i}", n) for i, n in enumerate(["اول", "دوم", "سوم", "چهارم", "پنجم", "ششم"], 1)]),
    ("MS1", "متوسطه دوره اول", [(f"G{i}", n) for i, n in ((7, "هفتم"), (8, "هشتم"), (9, "نهم"))]),
    ("MS2", "متوسطه دوره دوم", [(f"G{i}", n) for i, n in ((10, "دهم"), (11, "یازدهم"), (12, "دوازدهم"))]),
]
FIELDS = [("15000", "ریاضی فیزیک", "MS2"), ("15001", "علوم تجربی", "MS2"), ("15002", "علوم انسانی", "MS2"),
          ("15003", "فرهنگ و معارف اسلامی", "MS2"), ("14", "دوره متوسطه اول", "MS1")]


class Command(BaseCommand):
    help = "Seed levels, grades 1-12 and the upper-secondary fields (idempotent)."

    @transaction.atomic
    def handle(self, *args, **opts):
        levels = {}
        for order, (code, name, _) in enumerate(LEVELS, 1):
            levels[code], _ = Level.objects.get_or_create(code=code, defaults={"name": name, "order": order})
        for code, name, grades in LEVELS:
            for gcode, gname in grades:
                Grade.objects.get_or_create(code=gcode, defaults={"level": levels[code], "name": gname})
        for code, name, lcode in FIELDS:
            StudyField.objects.get_or_create(code=code, defaults={"name": name, "level": levels[lcode]})
        self.stdout.write(self.style.SUCCESS("Catalogue ready."))
