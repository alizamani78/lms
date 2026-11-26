"""Django-admin login with the same second factor as the API."""
from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from apps.accounts.mfa import get_confirmed_device, mfa_required_for, verify_and_consume


from django.core.cache import cache  # noqa: E402

ADMIN_MAX_FAILS = 5
ADMIN_LOCK_SECONDS = 15 * 60


class MfaAdminLoginForm(AdminAuthenticationForm):
    otp = forms.CharField(label="کد تأیید دو مرحله‌ای", required=False, max_length=12,
                          widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}))

    def clean(self):
        key = f"adminfail:{str(self.data.get('username', ''))[:40].lower()}"
        if cache.get(key, 0) >= ADMIN_MAX_FAILS:
            raise ValidationError("تلاش‌های ناموفق زیاد است؛ بعداً دوباره امتحان کنید.", code="locked")
        try:
            return super().clean()
        except ValidationError:
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, ADMIN_LOCK_SECONDS)
            raise

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        device = get_confirmed_device(user)
        if device:
            if not verify_and_consume(device, self.cleaned_data.get("otp", "")):
                raise ValidationError("کد تأیید دو مرحله‌ای نادرست یا خالی است.", code="otp")
        elif mfa_required_for(user):
            raise ValidationError(
                "برای نقش شما تأیید دو مرحله‌ای الزامی است. مدیر سامانه باید آن را فعال کند "
                "(py manage.py mfa_enroll).", code="mfa_required")
