from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.scope import ancestor_paths


class CalendarEvent(models.Model):
    """A holiday/closure/exam-period/event that applies to an org unit and EVERYTHING below it.
    org = NULL means nationwide (only super admins may create those)."""

    class Kind(models.TextChoices):
        HOLIDAY = "holiday", "تعطیل رسمی"
        CLOSURE = "closure", "تعطیلی (شرایط جوی / اضطراری)"
        EXAMS = "exams", "ایام امتحانات"
        EVENT = "event", "رویداد / مناسبت"

    org = models.ForeignKey("accounts.OrgUnit", null=True, blank=True, on_delete=models.CASCADE,
                            related_name="calendar_events", verbose_name="شمول (خالی = سراسر کشور)")
    title = models.CharField("عنوان", max_length=200)
    kind = models.CharField("نوع", max_length=8, choices=Kind.choices, default=Kind.EVENT)
    start_date = models.DateField("از تاریخ")
    end_date = models.DateField("تا تاریخ (شامل)")
    blocks_attendance = models.BooleanField("ثبت حضور و غیاب دستی در این روزها بسته باشد", default=False)
    description = models.CharField("توضیح", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "رویداد تقویم"
        verbose_name_plural = "رویدادهای تقویم آموزشی"
        indexes = [models.Index(fields=["start_date", "end_date"])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "تاریخ پایان نباید قبل از شروع باشد."})


def events_for_school(school, start, end):
    """Events that apply to `school` (nationwide + every unit above it) overlapping [start, end]."""
    return CalendarEvent.objects.filter(start_date__lte=end, end_date__gte=start).filter(
        Q(org__isnull=True) | Q(org__path__in=ancestor_paths(school.path)))


def blocking_event(school, day):
    return events_for_school(school, day, day).filter(blocks_attendance=True).first()


class Occasion(models.Model):
    """An official holiday or an occasion of the calendar (Jalali date stored, Gregorian date kept for queries)."""
    CATEGORY = [("holiday", "تعطیل رسمی"), ("national", "مناسبت ملی و فرهنگی"), ("religious", "مناسبت مذهبی"),
                ("school", "اختصاصی مدرسه یا سازمان")]
    title = models.CharField(max_length=200)
    jalali_year = models.PositiveSmallIntegerField()
    jalali_month = models.PositiveSmallIntegerField()
    jalali_day = models.PositiveSmallIntegerField()
    gregorian_date = models.DateField(db_index=True)
    category = models.CharField(max_length=10, choices=CATEGORY, default="holiday")
    is_holiday = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "مناسبت تقویم"
        verbose_name_plural = "مناسبت‌های تقویم"
        ordering = ["gregorian_date"]
        constraints = [models.UniqueConstraint(fields=["jalali_year", "jalali_month", "jalali_day", "title"], name="uniq_occasion_day_title")]

    def __str__(self):
        return f"{self.title} ({self.jalali_year}/{self.jalali_month:02d}/{self.jalali_day:02d})"
