from django.db import models


class DailyOrgStats(models.Model):
    """One pre-computed row per school per day. Province/region dashboards sum these rows instead of
    scanning the operational tables of 700,000 students."""
    date = models.DateField()
    org = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="daily_stats")
    students = models.PositiveIntegerField(default=0)
    att_total = models.PositiveIntegerField(default=0)
    att_present = models.PositiveIntegerField(default=0)       # present + late
    att_absent = models.PositiveIntegerField(default=0)
    exams_submitted = models.PositiveIntegerField(default=0)
    score_sum = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    score_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    submissions = models.PositiveIntegerField(default=0)
    sessions = models.PositiveIntegerField(default=0)
    sessions_failed = models.PositiveIntegerField(default=0)
    built_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "آمار روزانه مدرسه"
        verbose_name_plural = "آمار روزانه مدارس"
        constraints = [models.UniqueConstraint(fields=["date", "org"], name="uniq_stats_day_org")]
        indexes = [models.Index(fields=["org", "date"]), models.Index(fields=["date"])]
