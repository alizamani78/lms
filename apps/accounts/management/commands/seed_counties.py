"""Counties of Khorasan Razavi that are certain. This list is deliberately SHORT and conservative: counties change
(new ones are created, others move between provinces — for example Tabas belongs to South Khorasan).
Complete and correct it from the official list with the CSV import in the system tools, or add/edit counties by hand."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import OrgUnit

KHORASAN_RAZAVI = ["مشهد", "نیشابور", "سبزوار", "تربت حیدریه", "تربت جام", "کاشمر", "قوچان", "چناران", "گناباد", "بردسکن",
                   "خواف", "سرخس", "فریمان", "درگز", "کلات", "بجستان", "رشتخوار", "جغتای", "جوین", "خلیل‌آباد", "داورزن",
                   "مه‌ولات", "زاوه", "بینالود", "تایباد", "باخرز", "فیروزه"]


class Command(BaseCommand):
    help = "Create the certain counties of Khorasan Razavi (idempotent). Verify and complete them with the CSV import."

    @transaction.atomic
    def handle(self, *args, **opts):
        prov = OrgUnit.objects.filter(kind="province", name="خراسان رضوی", parent=None).first()
        if prov is None:
            raise CommandError("ابتدا استان‌ها را بسازید (seed_provinces).")
        n = 0
        for name in KHORASAN_RAZAVI:
            _, created = OrgUnit.objects.get_or_create(kind="county", name=name, parent=prov)
            n += created
        # wrong entries from the earlier version of this list are removed when nothing depends on them
        for bad in ("طبس", "نشتیفان", "کدکن"):
            OrgUnit.objects.filter(kind="county", name=bad, parent=prov, children__isnull=True, school_profiles__isnull=True).delete()
        self.stdout.write(self.style.SUCCESS(f"{n} شهرستان اضافه شد. فهرست را با منبع رسمی تطبیق دهید."))
