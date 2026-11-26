from django.conf import settings
from django.db import models
from django.db.models import Q


class TransferRequest(models.Model):
    """Student transfer between schools (needs the source school's release AND the destination school's acceptance)
    or between classes of one school (a single step by the school's admin). History of the student is never deleted."""

    class Status(models.TextChoices):
        PENDING_SOURCE = "pending_source", "در انتظار مدرسه مبدأ"
        PENDING_DEST = "pending_dest", "در انتظار مدرسه مقصد"
        READY = "ready", "تأییدشده؛ آماده اجرا"
        COMPLETED = "completed", "انجام شد"
        REJECTED = "rejected", "رد شد"
        CANCELLED = "cancelled", "لغو شد"

    ACTIVE = ("pending_source", "pending_dest", "ready")

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transfers")
    from_school = models.ForeignKey("accounts.OrgUnit", on_delete=models.PROTECT, related_name="+")
    to_school = models.ForeignKey("accounts.OrgUnit", on_delete=models.PROTECT, related_name="+")
    to_classroom = models.ForeignKey("academics.ClassRoom", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.PENDING_SOURCE, db_index=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    summary = models.JSONField(default=dict, blank=True)          # what the execution changed (audit)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "درخواست انتقال"
        verbose_name_plural = "درخواست‌های انتقال دانش‌آموز"
        constraints = [models.UniqueConstraint(fields=["student"], condition=Q(status__in=["pending_source", "pending_dest", "ready"]),
                                               name="one_active_transfer_per_student")]


class TransferEvent(models.Model):
    transfer = models.ForeignKey(TransferRequest, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=20)
    note = models.CharField(max_length=500, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class StudentSchoolHistory(models.Model):
    """One period a student spent in one school. Periods are never deleted: a transfer closes the current period and
    opens the next one in the new school, so the whole trail (school Z from X to Y, school Y from Y onward) is kept."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="school_history")
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.PROTECT, related_name="student_history",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    academic_year = models.ForeignKey("academics.AcademicYear", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="student_history", verbose_name="سال تحصیلی")
    start_date = models.DateField("از تاریخ")
    end_date = models.DateField("تا تاریخ", null=True, blank=True, help_text="خالی = هنوز در این مدرسه است")
    reason = models.CharField("علت", max_length=300, blank=True)
    transfer = models.ForeignKey(TransferRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="history")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سابقه مدرسه دانش‌آموز"
        verbose_name_plural = "سوابق مدرسه دانش‌آموزان"
        ordering = ["student_id", "start_date", "id"]
        indexes = [models.Index(fields=["student", "start_date"])]


def open_history(student, school, reason="", transfer=None, on_date=None):
    """Close the open period (if any) the day before, then open a new one in `school`."""
    from datetime import timedelta
    from django.utils import timezone
    from apps.academics.models import AcademicYear
    day = on_date or timezone.localdate()
    current = StudentSchoolHistory.objects.filter(student=student, end_date__isnull=True).first()
    if current is not None:
        if current.school_id == school.pk:
            return current
        current.end_date = max(day - timedelta(days=1), current.start_date)
        current.save(update_fields=["end_date"])
    year = AcademicYear.objects.filter(is_current=True).first()
    return StudentSchoolHistory.objects.create(student=student, school=school, academic_year=year, start_date=day,
                                               reason=reason[:300], transfer=transfer)
