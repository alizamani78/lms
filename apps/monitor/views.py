from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import audit

from .models import MonitorSetting, SystemMetric
from .sampler import sample


def _only_system_admin(request):
    if request.user.role != "super_admin":
        raise PermissionDenied("پایش سرور فقط برای مدیر سیستم است.")


class MonitorNowView(APIView):
    """GET — وضعیت لحظه‌ای سرور (CPU، RAM، شبکه، دیسک)."""

    def get(self, request):
        _only_system_admin(request)
        try:
            return Response(sample(seconds=1.0))
        except OSError:
            raise serializers.ValidationError({"detail": "این سرور اطلاعات /proc را ندارد؛ پایش فقط روی سرور لینوکسی کار می‌کند."})


class MonitorHistoryView(APIView):
    """GET ?hours=24 — نمونه‌های ثبت‌شده برای نمودار."""

    def get(self, request):
        _only_system_admin(request)
        hours = min(max(int(request.query_params.get("hours") or 24), 1), 24 * 30)
        rows = SystemMetric.objects.filter(ts__gte=timezone.now() - timedelta(hours=hours)).order_by("ts")[:5000]
        return Response([{"ts": r.ts.isoformat(), "cpu": r.cpu, "ram": r.ram, "rx": r.rx_kbps, "tx": r.tx_kbps,
                          "disk_used": r.disk_used_gb, "disk_total": r.disk_total_gb, "read": r.read_kbps, "write": r.write_kbps}
                         for r in rows])


class MonitorSettingView(APIView):
    """GET/PATCH — روشن یا خاموش کردن ثبت لاگ سیستم و مدت نگهداری."""

    def _get(self):
        s, _ = MonitorSetting.objects.get_or_create(pk=1)
        return s

    def get(self, request):
        _only_system_admin(request)
        s = self._get()
        return Response({"enabled": s.enabled, "keep_days": s.keep_days, "interval_minutes": s.interval_minutes,
                         "start_hour": s.start_hour, "end_hour": s.end_hour})

    def patch(self, request):
        _only_system_admin(request)
        s = self._get()
        if "enabled" in request.data:
            v = request.data.get("enabled")
            s.enabled = v in (True, "true", "1", 1, "on")
        if "keep_days" in request.data:
            s.keep_days = min(max(int(request.data.get("keep_days") or 30), 1), 365)
        if "interval_minutes" in request.data:
            s.interval_minutes = min(max(int(request.data.get("interval_minutes") or 1), 1), 60)
        if "start_hour" in request.data:
            s.start_hour = min(max(int(request.data.get("start_hour") or 0), 0), 23)
        if "end_hour" in request.data:
            s.end_hour = min(max(int(request.data.get("end_hour") or 24), 1), 24)
        s.save()
        audit(request, "monitor.setting", f"enabled:{s.enabled} keep:{s.keep_days}")
        return Response({"enabled": s.enabled, "keep_days": s.keep_days})
