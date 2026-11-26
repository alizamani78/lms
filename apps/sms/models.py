from django.conf import settings
from django.db import models


class SmsGateway(models.Model):
    """A school's SMS panel. The secret is stored ENCRYPTED; the API accepts a new secret but never returns it."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="sms_gateways",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    name = models.CharField("نام پنل", max_length=80)
    base_url = models.URLField("آدرس ارسال (https)", max_length=300)
    auth_header = models.CharField("نام هدر احراز هویت", max_length=40, default="Authorization")
    auth_secret_enc = models.TextField(editable=False, blank=True)
    body_template = models.TextField("قالب بدنه JSON", default='{"receptor": "{to}", "sender": "{sender}", "message": "{message}"}')
    sender = models.CharField("شماره/نام فرستنده", max_length=30, blank=True)
    is_active = models.BooleanField("فعال", default=False)
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_ok = models.BooleanField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "پنل پیامکی مدرسه"
        verbose_name_plural = "پنل‌های پیامکی مدارس"
        constraints = [models.UniqueConstraint(fields=["school"], condition=models.Q(is_active=True),
                                               name="one_active_sms_per_school")]

    def __str__(self):
        return f"{self.name} — {self.school.name}"


class SmsLog(models.Model):
    """Who was messaged, why and whether it was accepted. Never the message text or a code."""
    gateway = models.ForeignKey(SmsGateway, null=True, on_delete=models.SET_NULL, related_name="logs")
    to_masked = models.CharField(max_length=20)               # e.g. 0912***4567
    purpose = models.CharField(max_length=20)                  # recovery | test
    accepted = models.BooleanField()
    detail = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class PasswordResetCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reset_codes")
    code_hash = models.CharField(max_length=64)                # HMAC-SHA256, never the code itself
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]


class SmsOutbox(models.Model):
    """A message to a parent waiting to be sent (or already sent). One row per parent and event: the dedupe key stops
    the same event (same student, same day, same kind) from being sent twice."""
    STATUS = [("queued", "در صف"), ("sent", "ارسال شد"), ("failed", "ناموفق"), ("skipped", "بدون پنل پیامکی")]
    gateway = models.ForeignKey(SmsGateway, null=True, blank=True, on_delete=models.SET_NULL, related_name="outbox")
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="sms_outbox")
    parent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    purpose = models.CharField(max_length=30)
    mobile = models.CharField(max_length=15)
    text = models.CharField(max_length=500)
    dedupe_key = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=10, choices=STATUS, default="queued", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "پیامک در صف والدین"
        verbose_name_plural = "صف پیامک والدین"
        ordering = ["-created_at"]
