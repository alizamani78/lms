from celery import shared_task

from .notify import flush, scan_deadlines


@shared_task(name="apps.sms.tasks.parent_notifications")
def parent_notifications():
    """Every few minutes: look for finished deadlines, then send what is waiting in the outbox."""
    scan_deadlines()
    return flush()
