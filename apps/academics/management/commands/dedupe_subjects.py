"""حذف دروس تکراری (همنام در یک پایه). پیش‌نمایش پیش‌فرض است؛ با --apply اجرا می‌شود. قبل از اجرا پشتیبان بگیرید."""
from django.core.management.base import BaseCommand

from apps.academics.subject_dedupe import find_duplicates, merge
from apps.academics.models import Subject


class Command(BaseCommand):
    help = "ادغام دروس تکراری (همنام در یک پایه)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **o):
        groups = find_duplicates()
        self.stdout.write(f"گروه‌های تکراری: {len(groups)}")
        for g in groups:
            subs = list(Subject.objects.filter(pk__in=g["subjects"]).order_by("pk"))
            keep, drops = subs[0], subs[1:]
            self.stdout.write(f"- «{g['name']}» (پایه {g['grade']}): نگه داشتن #{keep.pk}، ادغام #{', #'.join(str(d.pk) for d in drops)}")
            if o["apply"]:
                for d in drops:
                    merge(keep, d)
        self.stdout.write("انجام شد." if o["apply"] else "پیش‌نمایش؛ با --apply اجرا کنید.")
