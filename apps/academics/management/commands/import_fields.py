"""Import study fields (کاردانش / فنی و حرفه‌ای / رشته‌ها) from CSV.
    py manage.py import_fields path/to/fields.csv
Columns: code,name,level_code   (level_code optional; must exist, e.g. MS2, KAR, FNI)
Re-running updates names of existing codes.
"""
import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academics.models import Level, StudyField
from apps.core.persian import normalize_text


class Command(BaseCommand):
    help = "Import study fields from a CSV file (code,name,level_code)."

    def add_arguments(self, parser):
        parser.add_argument("path")

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            with open(opts["path"], encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError as e:
            raise CommandError(str(e))
        created = updated = 0
        for r in rows:
            code, name = normalize_text(r.get("code")), normalize_text(r.get("name"))
            if not code or not name:
                raise CommandError(f"ردیف ناقص: {r}")
            level = None
            lcode = normalize_text(r.get("level_code"))
            if lcode:
                level = Level.objects.filter(code=lcode).first()
                if level is None:
                    raise CommandError(f"مقطع با کد {lcode} وجود ندارد.")
            obj, is_new = StudyField.objects.update_or_create(code=code, defaults={"name": name, "level": level})
            created += is_new
            updated += not is_new
        self.stdout.write(self.style.SUCCESS(f"{created} رشته اضافه شد، {updated} رشته به‌روز شد."))
