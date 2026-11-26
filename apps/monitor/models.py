from django.db import models


class MonitorSetting(models.Model):
    """تنظیمات پایش سرور (یک ردیف): ثبت لاگ دوره‌ای و مدت نگهداری."""
    enabled = models.BooleanField("ثبت لاگ سیستم", default=False)
    keep_days = models.PositiveSmallIntegerField("مدت نگهداری لاگ (روز)", default=30)
    interval_minutes = models.PositiveSmallIntegerField("فاصله‌ی ثبت (دقیقه)", default=1)
    start_hour = models.PositiveSmallIntegerField("از ساعت", default=0)
    end_hour = models.PositiveSmallIntegerField("تا ساعت", default=24)

    class Meta:
        verbose_name = "تنظیمات پایش"


class SystemMetric(models.Model):
    """یک نمونه از وضعیت سرور: پردازنده، حافظه، پهنای باند، ظرفیت و سرعت خواندن/نوشتن دیسک."""
    ts = models.DateTimeField("زمان", auto_now_add=True, db_index=True)
    cpu = models.FloatField("پردازنده (٪)")
    ram = models.FloatField("حافظه (٪)")
    ram_total_mb = models.PositiveIntegerField("حافظه‌ی کل (MB)", default=0)
    rx_kbps = models.FloatField("دریافت شبکه (KB/s)", default=0)
    tx_kbps = models.FloatField("ارسال شبکه (KB/s)", default=0)
    disk_used_gb = models.FloatField("دیسک پرشده (GB)", default=0)
    disk_total_gb = models.FloatField("ظرفیت دیسک (GB)", default=0)
    read_kbps = models.FloatField("خواندن دیسک (KB/s)", default=0)
    write_kbps = models.FloatField("نوشتن دیسک (KB/s)", default=0)

    class Meta:
        verbose_name = "نمونه‌ی پایش"
        ordering = ["-ts"]
