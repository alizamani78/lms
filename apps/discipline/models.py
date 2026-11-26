from django.conf import settings
from django.db import models
from django.utils import timezone

BASE_SCORE = 20          # every student starts with 20; the factors add (+) or subtract (−) points


class DisciplineFactor(models.Model):
    """A disciplinary factor (عامل انضباطی). school NULL = the default catalogue shared by all schools.
    points is negative for a deduction (کسر نمره) and positive for an addition (اضافه نمره)."""
    school = models.ForeignKey("accounts.OrgUnit", null=True, blank=True, on_delete=models.CASCADE, related_name="discipline_factors")
    title = models.CharField("عامل", max_length=200)
    points = models.DecimalField("مقدار نمره (− کسر، + اضافه)", max_digits=5, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "عامل انضباطی"
        verbose_name_plural = "عامل‌های انضباطی"
        ordering = ["points", "title"]
        constraints = [models.UniqueConstraint(fields=["school", "title"], name="uniq_discipline_factor_per_school")]

    def __str__(self):
        return f"{self.title} ({self.points})"


class DisciplineRecord(models.Model):
    """One entry in the discipline book of a student. The title and points are copied so that later changes of a
    factor do not rewrite history."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="discipline_records")
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="discipline_records")
    factor = models.ForeignKey(DisciplineFactor, null=True, blank=True, on_delete=models.SET_NULL, related_name="records")
    title = models.CharField(max_length=200)
    points = models.DecimalField(max_digits=5, decimal_places=2)
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    printed_at = models.DateTimeField("زمان چاپ کارنامه‌ی انضباطی", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ثبت انضباطی"
        verbose_name_plural = "دفتر انضباطی"
        ordering = ["-date", "-id"]
