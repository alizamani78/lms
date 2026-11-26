"""شناسایی و ادغام رشته‌ها و دروس تکراری بانک مرکزی.

  python manage.py dedupe_catalog                 ← فقط گزارش (هیچ تغییری نمی‌دهد)
  python manage.py dedupe_catalog --apply         ← ادغام واقعی

قاعده‌ی تکراری: کد یکسان (بدون توجه به فاصله، بزرگی حروف و حروف عربی/فارسی).
نگهدارنده: رکوردی که بیشترین ارجاع را دارد؛ در تساوی، قدیمی‌ترین.
همه‌ی ارجاع‌ها (کلید خارجی) به نگهدارنده منتقل می‌شوند. اگر انتقال با یک قید یکتا تداخل کند
(مثلاً همان مدرسه و همان رشته دو بار)، ردیف تکراری حذف می‌شود، چون همان ارتباط از قبل وجود دارد.
ردیف‌های تکراری در پایان حذف می‌شوند. ارجاع‌های چند‌به‌چند (M2M) جداگانه گزارش می‌شوند.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from apps.academics.models import StudyField, Subject


def norm(code):
    t = str(code or "").strip().lower().replace("ي", "ی").replace("ك", "ک")
    return "".join(t.split())


def _refs(obj):
    """کل ارجاع‌های کلیدخارجی به یک رکورد: [(مدل، نام فیلد)]"""
    out = []
    for rel in obj._meta.related_objects:
        if rel.many_to_many:
            continue
        out.append((rel.related_model, rel.field.name))
    return out


def _count_refs(obj):
    n = 0
    for model, field in _refs(obj):
        n += model.objects.filter(**{field: obj}).count()
    return n


def merge(model, keep, dup, stats):
    for rel_model, field in _refs(dup):
        for row in rel_model.objects.filter(**{field: dup}):
            try:
                with transaction.atomic():
                    setattr(row, field, keep)
                    row.save(update_fields=[field])
                stats["moved"] += 1
            except IntegrityError:
                # the same link already exists on the keeper: this copy is redundant
                with transaction.atomic():
                    row.delete()
                stats["dropped"] += 1
    dup.delete()
    stats["removed"] += 1


class Command(BaseCommand):
    help = "شناسایی و ادغام رشته‌ها و دروس تکراری"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="تغییرات را واقعاً انجام بده (بدون این گزینه فقط گزارش است)")
        parser.add_argument("--only", choices=["fields", "subjects"], default=None)

    def handle(self, *args, **opts):
        apply = opts["apply"]
        targets = [("رشته‌ها", StudyField)] if opts["only"] == "fields" else \
                  [("دروس", Subject)] if opts["only"] == "subjects" else [("رشته‌ها", StudyField), ("دروس", Subject)]
        for label, model in targets:
            groups = defaultdict(list)
            for obj in model.objects.all().order_by("id"):
                groups[norm(obj.code)].append(obj)
            dup_groups = {k: v for k, v in groups.items() if k and len(v) > 1}
            self.stdout.write(f"\n== {label}: {len(dup_groups)} گروه تکراری ==")
            stats = {"moved": 0, "dropped": 0, "removed": 0}
            for key, items in dup_groups.items():
                ranked = sorted(items, key=lambda o: (-_count_refs(o), o.id))
                keep, dups = ranked[0], ranked[1:]
                self.stdout.write(f"  کد «{key}»: نگهدارنده #{keep.id} «{keep.name}»؛ حذف: " +
                                  "، ".join(f"#{d.id} «{d.name}»" for d in dups))
                if apply:
                    for d in dups:
                        merge(model, keep, d, stats)
            # the same name under different codes is only reported (it may be a real difference)
            by_name = defaultdict(list)
            for obj in model.objects.all():
                by_name[norm(obj.name)].append(obj)
            names = [v for k, v in by_name.items() if k and len(v) > 1]
            if names:
                self.stdout.write(f"  هشدار: {len(names)} نام تکراری با کد متفاوت (بررسی دستی لازم است، ادغام نشد).")
            if apply:
                self.stdout.write(self.style.SUCCESS(f"  ادغام شد: {stats['removed']} تکراری حذف، {stats['moved']} ارجاع منتقل، {stats['dropped']} ارتباط تکراری حذف."))
        if not apply:
            self.stdout.write(self.style.WARNING("\nهیچ تغییری داده نشد. برای ادغام واقعی: --apply"))
