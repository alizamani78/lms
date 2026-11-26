from django.db.models import Q
from django.core.cache import cache
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.core.models import audit

from .mfa import get_confirmed_device, verify_and_consume
from .models import User

MAX_FAILS = 5
LOCK_SECONDS = 15 * 60


class LoginSerializer(TokenObtainPairSerializer):
    """Per-account lockout + optional TOTP step. Tokens carry `mfa: true` only when a code was verified."""

    @classmethod
    def get_token(cls, user):
        import uuid
        token = super().get_token(user)
        token["role"] = user.role
        # one active session per account: the new login replaces the old session id in the database
        sid = uuid.uuid4().hex
        user.session_key = sid
        user.save(update_fields=["session_key"])
        token["sid"] = sid
        return token

    def _fail(self, key):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, LOCK_SECONDS)

    def validate(self, attrs):
        # the login field may be the username OR the national id (کد ملی); the same account either way
        raw = str(attrs.get(self.username_field, "")).strip()
        from django.db.models import Q
        from .models import User as _U
        found = _U.objects.filter(Q(username=raw) | Q(national_id=raw)).first() if raw else None
        if found is not None:
            attrs[self.username_field] = found.username
        key = f"loginfail:{raw[:40]}"
        if cache.get(key, 0) >= MAX_FAILS:
            raise AuthenticationFailed("تلاش‌های ناموفق زیاد است؛ بعداً دوباره امتحان کنید.", code="locked")
        try:
            super().validate(attrs)                 # checks the password and sets self.user
        except AuthenticationFailed:
            self._fail(key)
            raise
        user, mfa_ok = self.user, False
        device = get_confirmed_device(user)
        if device:
            otp = str(self.initial_data.get("otp") or "")
            if not otp:
                raise serializers.ValidationError({"otp": "کد تأیید دو مرحله‌ای لازم است."})
            if not verify_and_consume(device, otp):
                self._fail(key)
                raise AuthenticationFailed("کد تأیید نادرست است.", code="otp_invalid")
            mfa_ok = True
        cache.delete(key)
        refresh = self.get_token(user)
        refresh["mfa"] = mfa_ok
        return {"refresh": str(refresh), "access": str(refresh.access_token)}


class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        from apps.core.models import AuditLog, client_ip
        resp = super().post(request, *args, **kwargs)
        name = str(request.data.get("username", "")).strip()[:150]
        user = User.objects.filter(Q(username=name) | Q(national_id=name)).first() if name else None
        if resp.status_code == 200 and user is not None:
            AuditLog.objects.create(user=user, action="auth.login", target="", ip=client_ip(request),
                                    request_id=getattr(request, "request_id", ""))
        elif resp.status_code in (400, 401):
            AuditLog.objects.create(user=user, action="auth.login_failed", target=name[:40], ip=client_ip(request),
                                    request_id=getattr(request, "request_id", ""))
        return resp


class RefreshView(TokenRefreshView):
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutView(APIView):
    def post(self, request):
        s = LogoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            RefreshToken(s.validated_data["refresh"]).blacklist()
        except Exception:
            pass   # an invalid/expired token is already unusable
        audit(request, "logout")
        return Response(status=204)
