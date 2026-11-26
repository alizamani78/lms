from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models


class Assignment(models.Model):
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="assignments", verbose_name="درس")
    title = models.CharField("عنوان", max_length=200)
    description = models.TextField("شرح تکلیف", blank=True, max_length=5000)
    due_at = models.DateTimeField("مهلت ارسال", null=True, blank=True)
    max_points = models.DecimalField("بیشترین نمره", max_digits=5, decimal_places=2, default=Decimal("20"))
    allow_late = models.BooleanField("پذیرش ارسال با تأخیر", default=False)
    opens_at = models.DateTimeField("شروع امکان ارسال", null=True, blank=True)
    audience = models.CharField("مخاطب", max_length=8, choices=[("all", "همه دانش‌آموزان درس"), ("selected", "دانش‌آموزان انتخاب‌شده")],
                                default="all")
    targets = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="targeted_assignments",
                                     verbose_name="دانش‌آموزان انتخاب‌شده")
    attachments = models.ManyToManyField("files.FileAsset", blank=True, related_name="+", verbose_name="فایل‌های پیوست")
    is_published = models.BooleanField("منتشرشده", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تکلیف"
        verbose_name_plural = "تکالیف"
        indexes = [models.Index(fields=["course", "due_at"])]

    def __str__(self):
        return self.title


class Submission(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="submissions")
    text = models.TextField(blank=True, max_length=20000)
    file_url = models.CharField(max_length=500, blank=True)      # optional https link
    files = models.ManyToManyField("files.FileAsset", blank=True, related_name="+")
    submitted_at = models.DateTimeField()
    is_late = models.BooleanField(default=False)
    points = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "پاسخ تکلیف"
        verbose_name_plural = "پاسخ‌های تکلیف"
        constraints = [models.UniqueConstraint(fields=["assignment", "student"], name="one_submission_per_student")]
        indexes = [models.Index(fields=["student", "submitted_at"])]


def validate_https_url(url):
    schemes = ["https"] + (["http"] if settings.DEBUG else [])
    try:
        URLValidator(schemes=schemes)(url)
    except ValidationError:
        raise ValidationError("آدرس معتبر با https وارد کنید.")
