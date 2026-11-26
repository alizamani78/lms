from celery import shared_task
from django.utils import timezone
from datetime import timedelta


@shared_task(name="apps.monitor.tasks.record_metrics")
def record_metrics():
    """هر دقیقه: اگر ثبت لاگ روشن باشد، یک نمونه ذخیره و لاگ‌های قدیمی‌تر از مدت نگهداری حذف می‌شوند."""
    from .models import MonitorSetting, SystemMetric
    from .sampler import sample
    setting = MonitorSetting.objects.first()
    if setting is None or not setting.enabled:
        return {"saved": 0}
    # the schedule: every N minutes, only between the chosen hours (Tehran time)
    now = timezone.localtime()
    if not (setting.start_hour <= now.hour < setting.end_hour):
        return {"saved": 0, "reason": "outside the hours"}
    if now.minute % max(1, setting.interval_minutes) != 0:
        return {"saved": 0, "reason": "not the interval"}
    data = sample(seconds=1.0)
    SystemMetric.objects.create(**data)
    SystemMetric.objects.filter(ts__lt=timezone.now() - timedelta(days=setting.keep_days)).delete()
    return {"saved": 1}
