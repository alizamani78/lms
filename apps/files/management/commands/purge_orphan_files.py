"""Delete uploaded files that were never attached to anything and are older than N hours (default 24).

    py manage.py purge_orphan_files [--hours 24]
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.courses.models import ContentItem
from apps.coursework.models import Assignment, Submission
from apps.files.models import FileAsset
from apps.files.views import storage_root


class Command(BaseCommand):
    help = "Remove unattached uploads."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=24)

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(hours=opts["hours"])
        used = set(ContentItem.objects.exclude(file=None).values_list("file_id", flat=True))
        used |= set(ContentItem.embedded_files.through.objects.values_list("fileasset_id", flat=True))
        used |= set(Assignment.attachments.through.objects.values_list("fileasset_id", flat=True))
        used |= set(Submission.files.through.objects.values_list("fileasset_id", flat=True))
        n = 0
        for a in FileAsset.objects.filter(created_at__lt=cutoff).exclude(pk__in=used):
            (storage_root() / a.stored_name[:2] / a.stored_name).unlink(missing_ok=True)
            a.delete()
            n += 1
        self.stdout.write(self.style.SUCCESS(f"{n} orphan file(s) removed."))
