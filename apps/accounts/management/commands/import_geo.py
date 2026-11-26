from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.persian import canonical, normalize_text
from apps.accounts.models import OrgUnit


class Command(BaseCommand):
    """py manage.py import_geo path.csv   columns: kind(county|region), code, name, parent_name, parent_code
       parent is looked up by code first, then by name. Regions may also sit directly under a province."""
    help = "Import counties and regions (with codes) from CSV."

    def add_arguments(self, parser):
        parser.add_argument("path")

    @transaction.atomic
    def handle(self, *args, **opts):
        import csv
        try:
            with open(opts["path"], encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        except OSError as e:
            raise CommandError(str(e))
        created = 0
        for r in rows:
            kind = normalize_text(r.get("kind"))
            name = normalize_text(r.get("name"))
            code = normalize_text(r.get("code")) or None
            if kind not in ("county", "region") or not name:
                raise CommandError(f"ردیف نامعتبر: {r}")
            pcode, pname = normalize_text(r.get("parent_code")), normalize_text(r.get("parent_name"))
            parent = (OrgUnit.objects.filter(code=pcode).first() if pcode else None) or \
                     (next((o for o in OrgUnit.objects.filter(kind__in=["province", "county"])
                            if canonical(o.name) == canonical(pname)), None) if pname else None)
            if parent is None:
                raise CommandError(f"بالاتر برای «{name}» پیدا نشد.")
            if code:
                obj, is_new = OrgUnit.objects.update_or_create(code=code, defaults={"name": name, "kind": kind, "parent": parent})
            else:
                obj, is_new = OrgUnit.objects.get_or_create(kind=kind, name=name, parent=parent)
            created += is_new
        self.stdout.write(self.style.SUCCESS(f"{created} واحد اضافه شد."))
