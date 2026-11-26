import os

from django.core.validators import RegexValidator
from django.db import models


class ExternalSystem(models.Model):
    """Connection settings for BigBlueButton, Adobe Connect, Skyroom, SMS gateway, SSO, ...

    Secrets are NEVER stored here: `secret_env` names an environment variable (or secret-manager injected
    variable) that holds the secret on the server.
    """

    class Kind(models.TextChoices):
        BBB = "bbb", "BigBlueButton"
        ADOBE = "adobe", "Adobe Connect"
        SKYROOM = "skyroom", "Skyroom"
        SMS = "sms", "درگاه پیامک"
        SSO = "sso", "ورود یکپارچه (SSO)"
        OTHER = "other", "سایر"

    name = models.CharField("نام اتصال", max_length=100)
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices)
    # a plain text field: Adobe Connect hosts are given without a scheme (school.adobeconnect.com)
    base_url = models.CharField("آدرس پایه (API) یا هاست", max_length=300, blank=True, default="")
    secret_env = models.CharField(
        "نام متغیر محیطی راز", max_length=64, blank=True,
        validators=[RegexValidator(r"^[A-Z][A-Z0-9_]*$",
                                   "اینجا فقط «نام» متغیر را بنویسید، مثلاً BBB_SECRET (با حروف بزرگ انگلیسی، عدد و _). خود رمز را اینجا ننویسید.")],
        help_text="نام متغیری که رمز در فایل تنظیمات سرور دارد، مثلاً BBB_SECRET؛ خود رمز اینجا وارد نمی‌شود.")
    # سامانه‌ی اختصاصی یک مدرسه (مدیر یا معاون آن را تعریف می‌کند)؛ خالی یعنی سراسری (مدیر سامانه)
    school = models.ForeignKey("accounts.OrgUnit", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="external_systems", verbose_name="مدرسه")
    is_active = models.BooleanField("فعال", default=True)
    notes = models.TextField("توضیحات", blank=True)
    last_check_at = models.DateTimeField("آخرین بررسی", null=True, blank=True, editable=False)
    last_check_ok = models.BooleanField("نتیجه بررسی", null=True, editable=False)
    last_check_detail = models.CharField("جزئیات بررسی", max_length=200, blank=True, editable=False)

    class Meta:
        verbose_name = "سامانه خارجی"
        verbose_name_plural = "سامانه‌های خارجی و ارتباطات"
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_external_system_name_per_school")]


    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        raw = (self.secret_env or "").strip()
        # رمز واقعی را در فیلد نام متغیر (حروف کوچک یا خیلی طولانی) نپذیر
        if raw and (raw != raw.upper() or len(raw) > 40 or any(c in raw for c in "/:@")):
            errors["secret_env"] = "اینجا رمز نوشته شده است. فقط نام متغیر را بنویسید، مثلاً BBB_SECRET؛ رمز را در فایل /etc/razavi-lms/.env قرار دهید."
        if self.kind == "bbb":
            if not (self.base_url or "").strip().startswith("https://"):
                errors["base_url"] = "آدرس BigBlueButton باید با https:// شروع شود، مثلاً https://webinar01.razaviedu.ir/bigbluebutton"
            if not (self.base_url or "").rstrip("/").endswith("/bigbluebutton"):
                errors.setdefault("base_url", "آدرس باید به /bigbluebutton ختم شود (بدون /api).")
            # the secret is either typed by the school (stored encrypted) or named as an environment variable
            if not (self.secret_enc or raw):
                errors["secret"] = "رمز سامانه (Secret) را وارد کنید."
        if self.kind == "adobe":
            if not (self.base_url or "").strip():
                errors["base_url"] = "هاست Adobe Connect را وارد کنید، مثلاً school.adobeconnect.com."
            if not (self.admin_user or "").strip():
                errors["admin_user"] = "نام کاربری مدیر Adobe Connect را وارد کنید."
            if not (self.admin_password_enc or raw):
                errors["admin_password"] = "رمز مدیر Adobe Connect را وارد کنید."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    # the school's own secret (encrypted in the database; the key comes from the server, never from the browser)
    secret_enc = models.TextField("رمز سامانه (رمزگذاری‌شده)", blank=True, default="", editable=False)
    admin_user = models.CharField("نام کاربری مدیر (Adobe)", max_length=100, blank=True, default="")
    admin_password_enc = models.TextField("رمز مدیر (رمزگذاری‌شده)", blank=True, default="", editable=False)

    @staticmethod
    def _fernet():
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        from django.conf import settings
        raw = os.environ.get("LMS_FIELD_KEY", "")
        key = raw.encode() if raw else base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
        return Fernet(key)

    def set_secret(self, value):
        self.secret_enc = self._fernet().encrypt(value.encode()).decode() if value else ""

    def set_admin_password(self, value):
        self.admin_password_enc = self._fernet().encrypt(value.encode()).decode() if value else ""

    def get_admin_password(self):
        from cryptography.fernet import InvalidToken
        if not self.admin_password_enc:
            return ""
        try:
            return self._fernet().decrypt(self.admin_password_enc.encode()).decode()
        except InvalidToken:
            return ""

    def secret(self):
        """the school's own secret if it was entered, otherwise the server's environment variable"""
        if self.secret_enc:
            from cryptography.fernet import InvalidToken
            try:
                return self._fernet().decrypt(self.secret_enc.encode()).decode()
            except InvalidToken:
                return ""
        return os.environ.get(self.secret_env, "") if self.secret_env else ""

    def secret_configured(self):
        return bool(self.secret())


class AdobeAccount(models.Model):
    """حساب Adobe Connect هر کاربر در سامانه‌ی یک مدرسه (ساخته‌شده توسط سامانه؛ رمز آن رمزگذاری‌شده ذخیره می‌شود)."""
    system = models.ForeignKey(ExternalSystem, on_delete=models.CASCADE, related_name="adobe_accounts")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="adobe_accounts")
    login = models.CharField(max_length=150)
    principal_id = models.CharField(max_length=40, blank=True, default="")
    password_enc = models.TextField(blank=True, default="", editable=False)

    class Meta:
        verbose_name = "حساب Adobe کاربر"
        constraints = [models.UniqueConstraint(fields=["system", "user"], name="uniq_adobe_account")]

    def set_password(self, value):
        self.password_enc = ExternalSystem._fernet().encrypt(value.encode()).decode()

    def get_password(self):
        return ExternalSystem._fernet().decrypt(self.password_enc.encode()).decode() if self.password_enc else ""

