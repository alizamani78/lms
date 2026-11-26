import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class ClassSession(models.Model):
    class Provider(models.TextChoices):
        BBB = "bbb", "BigBlueButton"
        ADOBE = "adobe", "Adobe Connect"
        SKYROOM = "skyroom", "Skyroom"

    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="sessions", verbose_name="درس")
    title = models.CharField("عنوان جلسه", max_length=200)
    provider = models.CharField("سرویس کلاس آنلاین", max_length=10, choices=Provider.choices, default=Provider.BBB)
    system = models.ForeignKey("integrations.ExternalSystem", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="sessions", limit_choices_to={"kind__in": ["bbb", "adobe", "skyroom"]},
                               verbose_name="سامانه متصل")
    external_id = models.CharField("شناسه جلسه در سرویس", max_length=100, blank=True)
    starts_at = models.DateTimeField("شروع")
    ends_at = models.DateTimeField("پایان")
    auto_create = models.BooleanField("ساخت خودکار اتاق در سرویس (از طریق صف)", default=False)
    # دسترسی دانش‌آموزان در این جلسه (دبیر، معاون و مدیر همیشه همه‌ی امکانات را دارند)
    student_perms = models.JSONField("دسترسی دانش‌آموزان", default=dict, blank=True)

    PERM_KEYS = ("mic", "cam", "chat", "share", "file", "record")

    def can(self, name):
        """دانش‌آموز این امکان را دارد؟ پیش‌فرض: خیر (امن‌ترین حالت)."""
        return bool((self.student_perms or {}).get(name, False))
    remote_status = models.CharField("وضعیت اتاق", max_length=8, default="manual", editable=False, choices=[
        ("manual", "دستی"), ("pending", "در صف ساخت"), ("ready", "آماده"), ("failed", "ناموفق")])
    create_attempts = models.PositiveSmallIntegerField(default=0, editable=False)
    last_error = models.CharField(max_length=200, blank=True, editable=False)
    min_presence_percent = models.PositiveSmallIntegerField("حداقل درصد حضور برای «حاضر»", default=50)
    attendance_status = models.CharField("وضعیت حضور و غیاب", max_length=10, default="none", editable=False, choices=[
        ("none", "—"), ("finalized", "نهایی‌شده"), ("no_data", "سرویس گزارش ورود/خروج نداد"),
        ("no_class", "درس به کلاس وصل نیست")])

    class Meta:
        verbose_name = "جلسه کلاس آنلاین"
        verbose_name_plural = "جلسات کلاس آنلاین"
        indexes = [models.Index(fields=["course", "starts_at"])]

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "پایان باید بعد از شروع باشد."})
        if self.system_id and self.system.kind != self.provider:
            raise ValidationError({"system": "نوع سامانه با سرویس انتخاب‌شده یکی نیست."})

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.auto_create and self._state.adding:
            if not self.external_id:
                self.external_id = f"lms-{uuid.uuid4().hex}"       # stable id => retries never create a 2nd room
            self.remote_status = "pending"
        super().save(*args, **kwargs)


class ProviderEvent(models.Model):
    """Raw, de-duplicated events pushed by a provider (join/leave/meeting-ended)."""
    system = models.ForeignKey("integrations.ExternalSystem", on_delete=models.CASCADE, related_name="events")
    external_event_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=30)
    session = models.ForeignKey(ClassSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="events")
    user_ext = models.CharField(max_length=40, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default="received")      # received | processed | ignored | error
    error = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "رویداد سرویس بیرونی"
        verbose_name_plural = "رویدادهای سرویس‌های بیرونی"
        constraints = [models.UniqueConstraint(fields=["system", "external_event_id"], name="uniq_provider_event")]
        indexes = [models.Index(fields=["session", "occurred_at"])]


class OnlinePresence(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="presence")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    first_join = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    open_since = models.DateTimeField(null=True, blank=True)       # joined and not yet left
    total_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "حضور در کلاس آنلاین"
        verbose_name_plural = "حضور در کلاس‌های آنلاین"
        constraints = [models.UniqueConstraint(fields=["session", "student"], name="uniq_presence")]


class ClassRecording(models.Model):
    """ضبط یک جلسه‌ی آنلاین. فایل روی سرور بیگ‌بلو است؛ اعتبار (expires_at) پس از پایان حذف از سرور می‌شود."""
    STATUS = [("active", "فعال"), ("deleted", "حذف‌شده")]
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="recordings", verbose_name="جلسه")
    remote_id = models.CharField("شناسه‌ی ضبط در سرور", max_length=200)
    playback_url = models.URLField("نشانی دانلود/مشاهده", max_length=500, blank=True, default="")
    recorded_at = models.DateTimeField("زمان ضبط", null=True, blank=True)
    retention_days = models.PositiveSmallIntegerField("مدت اعتبار (روز)", default=2)
    warned_at = models.DateTimeField("زمان هشدار حذف به مسئولان", null=True, blank=True)
    expires_at = models.DateTimeField("تاریخ حذف خودکار", null=True, blank=True)
    status = models.CharField("وضعیت", max_length=10, choices=STATUS, default="active")
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ضبط جلسه"
        constraints = [models.UniqueConstraint(fields=["session", "remote_id"], name="uniq_recording_per_session")]

