from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.geo import PROVINCES  # noqa: F401
from apps.core.persian import canonical, normalize_text

from apps.accounts.models import OrgUnit


class Command(BaseCommand):
    help = "Create the 31 provinces of Iran (idempotent). Counties and regions are imported with import_geo."

    @transaction.atomic
    def handle(self, *args, **opts):
        n = 0
        for name in PROVINCES:
            _, created = OrgUnit.objects.get_or_create(kind="province", name=name, parent=None)
            n += created
        self.stdout.write(self.style.SUCCESS(f"{n} استان اضافه شد."))



