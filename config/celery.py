import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("lms")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.on_after_configure.connect
def periodic_tasks(sender, **kwargs):
    """Run exactly ONE `celery beat` instance. (Auto-submit of expired exams is scheduled in settings.)"""
    sender.add_periodic_task(300.0, app.signature("apps.liveclass.tasks.finalize_ended_sessions"),
                             name="finalize-ended-sessions")
    sender.add_periodic_task(3600.0, app.signature("apps.liveclass.tasks.purge_expired_recordings"),
                             name="purge-expired-recordings")
    sender.add_periodic_task(1800.0, app.signature("apps.liveclass.tasks.warn_expiring_recordings"),
                             name="warn-expiring-recordings")
    sender.add_periodic_task(900.0, app.signature("apps.liveclass.tasks.sync_adobe_attendance"),
                             name="sync-adobe-attendance")
    sender.add_periodic_task(60.0, app.signature("apps.monitor.tasks.record_metrics"),
                             name="record-server-metrics")
    sender.add_periodic_task(86400.0, app.signature("apps.liveclass.tasks.purge_old_online_history"),
                             name="purge-old-online-history")
    sender.add_periodic_task(crontab(hour=2, minute=30), app.signature("apps.reports.tasks.build_daily_stats"),
                             name="build-daily-stats")
