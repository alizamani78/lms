from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import Attempt
from .services import GRACE_SECONDS, grade_and_close


@shared_task
def auto_submit_expired(batch=500):
    """Close attempts whose deadline passed. Safe to run concurrently (skip_locked) and repeatedly."""
    cutoff = timezone.now() - timedelta(seconds=GRACE_SECONDS + 30)
    done = 0
    with transaction.atomic():
        rows = (Attempt.objects.select_for_update(skip_locked=True)
                .filter(submitted_at__isnull=True, deadline__lt=cutoff)[:batch])
        for attempt in rows:
            grade_and_close(attempt)
            done += 1
    return done
