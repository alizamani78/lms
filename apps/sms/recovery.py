"""Password recovery by SMS for students, teachers and parents.
Anti-abuse: one code per 60 s and 5 per hour per account; 5 wrong tries per code; 5-minute expiry;
every request answers the same way whether or not the account exists (no user enumeration)."""
import re
import secrets
from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoVE

from apps.accounts.models import User
from apps.core.models import audit

from .crypto import code_hash
from .models import PasswordResetCode, SmsGateway, SmsLog
from .providers import SmsError, send

CODE_TTL = timedelta(minutes=5)
MAX_TRIES = 5
GENERIC = "اگر حساب با این مشخصات در سامانه ثبت شده باشد، کد یک‌بار مصرف به شماره ثبت‌شده پیامک می‌شود."
MOBILE = re.compile(r"^09\d{9}$")


def normalize_mobile(value):
    v = (value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")).strip()
    v = re.sub(r"[\s\-]", "", v)
    if v.startswith("+98"):
        v = "0" + v[3:]
    return v if MOBILE.match(v) else None


def mask(mobile):
    return f"{mobile[:4]}***{mobile[-4:]}"


def gateway_for(user):
    """The active SMS panel of the user's school (students/teachers) — None when the school has none."""
    if user.org_id is None:
        return None
    return SmsGateway.objects.filter(school_id=user.org_id, is_active=True).first()


def _find_user(username):
    return User.objects.filter(username=username, is_active=True).exclude(mobile="").first()


class RecoveryRequestView(APIView):
    """POST {username, mobile} -> always 202 with the generic message."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        username = str(request.data.get("username", ""))[:30].strip()
        mobile = normalize_mobile(str(request.data.get("mobile", "")))
        if not username or not mobile:
            raise serializers.ValidationError({"detail": "نام کاربری و شماره موبایل لازم است."})
        user = _find_user(username)
        if user and normalize_mobile(user.mobile) == mobile:
            self._issue(user, request)
        return Response({"detail": GENERIC}, status=202)

    def _issue(self, user, request):
        gw = gateway_for(user)
        if gw is None:
            return
        if not cache.add(f"sms-cool:{user.pk}", 1, 60):
            return
        hour_key = f"sms-hour:{user.pk}"
        cache.add(hour_key, 0, 3600)
        if cache.incr(hour_key) > 5:
            return
        code = f"{secrets.randbelow(1_000_000):06d}"
        with transaction.atomic():
            PasswordResetCode.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
            PasswordResetCode.objects.create(user=user, code_hash=code_hash(user.pk, code),
                                             expires_at=timezone.now() + CODE_TTL)
        msg = f"کد بازیابی رمز سامانه آموزش رضوی: {code}\nاعتبار: ۵ دقیقه. اگر درخواست شما نبود، پیامک را نادیده بگیرید."
        ok, detail = False, "send error"
        try:
            ok, detail = send(gw, user.mobile, msg)
        except SmsError as e:
            detail = str(e)[:100]
        SmsLog.objects.create(gateway=gw, to_masked=mask(user.mobile), purpose="recovery", accepted=ok, detail=detail[:120])


class RecoveryVerifyView(APIView):
    """POST {username, code, new_password} -> password changed, every old session revoked."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        username = str(request.data.get("username", ""))[:30].strip()
        code = re.sub(r"\D", "", str(request.data.get("code", "")))[:6]
        new_password = str(request.data.get("new_password", ""))[:128]
        user = User.objects.filter(username=username, is_active=True).first()
        generic = serializers.ValidationError({"detail": "کد نادرست یا منقضی است."})
        if user is None or len(code) != 6:
            raise generic
        with transaction.atomic():
            rec = PasswordResetCode.objects.select_for_update().filter(
                user=user, used_at__isnull=True, expires_at__gt=timezone.now()).order_by("-created_at").first()
            if rec is None or rec.attempts >= MAX_TRIES:
                raise generic
            if not secrets.compare_digest(rec.code_hash, code_hash(user.pk, code)):
                rec.attempts += 1
                rec.save(update_fields=["attempts"])
                raise generic
            try:
                validate_password(new_password, user)
            except DjangoVE as e:
                raise serializers.ValidationError({"new_password": list(e.messages)})
            rec.used_at = timezone.now()
            rec.save(update_fields=["used_at"])
            user.set_password(new_password)
            user.save(update_fields=["password"])
            for t in OutstandingToken.objects.filter(user=user):
                BlacklistedToken.objects.get_or_create(token=t)
        cache.delete(f"loginfail:{username}")
        audit(request, "password.recovered", f"user:{user.pk}")
        return Response({"detail": "رمز عبور تغییر کرد. با رمز جدید وارد شوید."})
