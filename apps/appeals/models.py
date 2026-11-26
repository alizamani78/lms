from django.conf import settings
from django.db import models


class GradeAppeal(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "در انتظار بررسی معلم"
        REJECTED = "rejected", "رد شد (قابل ارجاع به مدیر)"
        ESCALATED = "escalated", "ارجاع به مدیر"
        ACCEPTED = "accepted", "پذیرفته شد"
        REJECTED_FINAL = "rejected_final", "رد نهایی"
        WITHDRAWN = "withdrawn", "پس گرفته شد"

    class Kind(models.TextChoices):
        ANSWER = "answer", "پاسخ تشریحی"
        SUBMISSION = "submission", "تکلیف"
        ATTEMPT = "attempt", "کل آزمون"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="appeals")
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="appeals")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    ref_id = models.BigIntegerField()
    text = models.TextField(max_length=2000)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN, db_index=True)
    response = models.TextField(blank=True, max_length=1000)
    old_points = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    new_points = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        verbose_name = "اعتراض به نمره"
        verbose_name_plural = "اعتراض‌ها به نمره"
        constraints = [models.UniqueConstraint(fields=["student", "kind", "ref_id"], name="one_appeal_per_item")]
        indexes = [models.Index(fields=["course", "status"])]


class AppealEvent(models.Model):
    """Append-only trail: filed, decided, escalated, withdrawn."""
    appeal = models.ForeignKey(GradeAppeal, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=20)
    note = models.CharField(max_length=500, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
