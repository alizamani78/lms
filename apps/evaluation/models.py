from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

DEFAULT_BANDS = [{"min": 85, "label": "عالی"}, {"min": 70, "label": "خوب"}, {"min": 50, "label": "متوسط"}, {"min": 0, "label": "ضعیف"}]


class EvaluationPolicy(models.Model):
    """Pass mark and rating bands. Each level may set its own (school, region, county, province); the most specific
    one that covers a school applies; a policy without an organisation is the national default."""
    org = models.ForeignKey("accounts.OrgUnit", null=True, blank=True, on_delete=models.CASCADE,
                            related_name="evaluation_policies", verbose_name="سطح (واحد سازمانی)")
    pass_percent = models.DecimalField("حد قبولی (٪)", max_digits=5, decimal_places=2, default=50)
    continuous_weight = models.DecimalField("ضریب نمره مستمر", max_digits=5, decimal_places=2, default=1)
    final_weight = models.DecimalField("ضریب نمره پایانی", max_digits=5, decimal_places=2, default=1)
    bands = models.JSONField("بازه‌های ارزشیابی", default=list, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "سیاست ارزشیابی"
        verbose_name_plural = "سیاست‌های ارزشیابی"
        constraints = [models.UniqueConstraint(fields=["org"], name="one_policy_per_org")]

    def clean(self):
        if float(self.continuous_weight) < 0 or float(self.final_weight) < 0 or \
                float(self.continuous_weight) + float(self.final_weight) <= 0:
            raise ValidationError({"final_weight": "ضرایب باید صفر یا بیشتر باشند و مجموعشان مثبت باشد."})
        if not 0 <= float(self.pass_percent) <= 100:
            raise ValidationError({"pass_percent": "حد قبولی بین ۰ و ۱۰۰ است."})
        bands = self.bands or DEFAULT_BANDS
        mins = [float(b.get("min", -1)) for b in bands]
        if any(not (0 <= m <= 100) for m in mins) or mins != sorted(mins, reverse=True) or 0 not in mins:
            raise ValidationError({"bands": "بازه‌ها باید از بالا به پایین مرتب باشند و کمترین حد صفر باشد."})


class ClassGrade(models.Model):
    """نمره سرکلاسی: a mark given in class (not an exam), e.g. a quiz, a presentation or participation.
    period tells which mark it is: monthly, mid-term, final, or in-class."""
    PERIOD = [("class", "سرکلاسی"), ("monthly", "ماهانه"), ("midterm", "میان ترم"), ("final", "پایان ترم")]
    period = models.CharField(max_length=10, choices=PERIOD, default="class")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="class_grades")
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="class_grades")
    title = models.CharField(max_length=200)
    date = models.DateField()
    score = models.DecimalField(max_digits=6, decimal_places=2)
    max_score = models.DecimalField(max_digits=6, decimal_places=2, default=20)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "نمره سرکلاسی"
        verbose_name_plural = "نمره‌های سرکلاسی"
        ordering = ["-date", "-id"]


class GraceAward(models.Model):
    """ارفاق: points (percentage points) added to a student's mark in a course, with the reason."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="grace_awards")
    points = models.DecimalField(max_digits=5, decimal_places=2)
    reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ارفاق"
