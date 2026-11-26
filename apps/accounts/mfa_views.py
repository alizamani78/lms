from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import audit

from .mfa import (encrypt, get_confirmed_device, mfa_required_for, new_secret, provisioning_uri,
                  verify_and_consume)
from .models import TOTPDevice


class MfaView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class CodeInput(serializers.Serializer):
    code = serializers.CharField(max_length=12)


class MfaSetupView(MfaView):
    def post(self, request):
        if get_confirmed_device(request.user):
            return Response({"detail": "تأیید دو مرحله‌ای قبلاً فعال شده است."}, status=409)
        secret = new_secret()
        TOTPDevice.objects.update_or_create(
            user=request.user, defaults={"secret_enc": encrypt(secret), "confirmed": False, "last_step": 0})
        return Response({"secret": secret, "otpauth_uri": provisioning_uri(request.user, secret)})


class MfaConfirmView(MfaView):
    def post(self, request):
        s = CodeInput(data=request.data)
        s.is_valid(raise_exception=True)
        device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
        if device is None or not verify_and_consume(device, s.validated_data["code"]):
            return Response({"detail": "کد نادرست است."}, status=400)
        device.confirmed = True
        device.save(update_fields=["confirmed"])
        audit(request, "mfa.enabled")
        import uuid
        refresh = RefreshToken.for_user(request.user)
        refresh["mfa"] = True
        refresh["role"] = request.user.role
        request.user.session_key = uuid.uuid4().hex
        request.user.save(update_fields=["session_key"])
        refresh["sid"] = request.user.session_key
        return Response({"access": str(refresh.access_token), "refresh": str(refresh)})


class DisableInput(serializers.Serializer):
    password = serializers.CharField(max_length=128)
    code = serializers.CharField(max_length=12)


class MfaDisableView(MfaView):
    def post(self, request):
        if mfa_required_for(request.user):
            return Response({"detail": "برای نقش شما تأیید دو مرحله‌ای الزامی است."}, status=403)
        s = DisableInput(data=request.data)
        s.is_valid(raise_exception=True)
        device = get_confirmed_device(request.user)
        if device is None:
            return Response(status=204)
        if not request.user.check_password(s.validated_data["password"]) or \
                not verify_and_consume(device, s.validated_data["code"]):
            return Response({"detail": "رمز یا کد نادرست است."}, status=400)
        device.delete()
        audit(request, "mfa.disabled")
        return Response(status=204)
