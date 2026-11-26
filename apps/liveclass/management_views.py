"""مدیریت جلسات آنلاین: ویرایش و حذف جلسه، و ضبط‌ها (فهرست، همگام‌سازی، مدت اعتبار، حذف)."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.jalali import format_jalali
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.roster import roster_ids

from . import recordings as R
from .models import ClassRecording, ClassSession

MANAGERS = ("super_admin", "school_admin", "school_deputy_edu")


def _can_manage(user, session):
    if user.role == "teacher" and session.course.teacher_id == user.pk:
        return True
    return user.role in MANAGERS and org_in_scope(user, session.course.org)


def _can_view_recordings(user, session):
    if _can_manage(user, session):
        return True
    return user.role == "student" and user.pk in roster_ids(session.course)


class SessionItemView(APIView):
    """PATCH — ویرایش عنوان، زمان، دسترسی دانش‌آموزان، درصد حضور. DELETE — حذف جلسه (فقط وقتی ضبط فعالی ندارد)."""

    def patch(self, request, pk):
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher"), pk=pk)
        if not _can_manage(request.user, s):
            raise PermissionDenied("جلسه را دبیر همان درس یا مسئول مدرسه ویرایش می‌کند.")
        data = request.data
        if "title" in data:
            s.title = str(data["title"]).strip()[:200]
        if "starts_at" in data:
            s.starts_at = data["starts_at"]
        if "ends_at" in data:
            s.ends_at = data["ends_at"]
        if s.ends_at and s.starts_at and s.ends_at <= s.starts_at:
            raise serializers.ValidationError({"ends_at": "پایان جلسه باید بعد از شروع باشد."})
        if "student_perms" in data and isinstance(data["student_perms"], dict):
            s.student_perms = {k: bool(v) for k, v in data["student_perms"].items() if k in ClassSession.PERM_KEYS}
        if "min_presence_percent" in data:
            s.min_presence_percent = int(data["min_presence_percent"])
        s.save()
        audit(request, "live.edit", f"session:{s.pk}")
        return Response({"id": s.pk, "note": "تغییر دسترسی‌ها و نام، از دفعه‌ی بعدی ورود اعمال می‌شود؛ زمان‌بندی اتاق فعلی بیگ‌بلو دست‌نخورده می‌ماند."})

    def delete(self, request, pk):
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher"), pk=pk)
        if not _can_manage(request.user, s):
            raise PermissionDenied("جلسه را دبیر همان درس یا مسئول مدرسه حذف می‌کند.")
        if s.recordings.filter(status="active").exists():
            return Response({"ok": False, "errors": ["این جلسه ضبط فعال دارد. ابتدا ضبط‌ها را حذف کنید یا منتظر پایان مدت اعتبارشان بمانید."]}, status=409)
        audit(request, "live.delete", f"session:{s.pk}")
        s.delete()
        return Response(status=204)


def _rec_row(r):
    return {"id": r.pk, "status": r.status, "status_label": r.get_status_display(),
            "recorded_at": format_jalali(r.recorded_at) if r.recorded_at else "",
            "expires_at": format_jalali(r.expires_at) if r.expires_at else "",
            "retention_days": r.retention_days,
            "playback_url": r.playback_url if r.status == "active" else ""}


class RecordingListView(APIView):
    """GET — ضبط‌های جلسه (دانش‌آموز درس، و دبیر/مدیر). POST — همگام‌سازی از سرور بیگ‌بلو."""

    def get(self, request, pk):
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher"), pk=pk)
        if not _can_view_recordings(request.user, s):
            raise PermissionDenied("ضبط این جلسه برای دانش‌آموزان همین درس است.")
        return Response([_rec_row(r) for r in s.recordings.order_by("-recorded_at")])

    def post(self, request, pk):
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher", "system"), pk=pk)
        if not _can_manage(request.user, s):
            raise PermissionDenied("همگام‌سازی ضبط را دبیر یا مسئول مدرسه انجام می‌دهد.")
        try:
            made = R.sync(s, retention_days=R.DEFAULT_RETENTION_DAYS)
        except RuntimeError as e:
            return Response({"ok": False, "errors": [f"اتصال به سرویس ضبط ممکن نشد: {e}"]}, status=503)
        except Exception:
            return Response({"ok": False, "errors": ["سرویس ضبط در دسترس نیست؛ بعداً دوباره امتحان کنید."]}, status=503)
        return Response({"ok": True, "new": made})


class RecordingItemView(APIView):
    """PATCH {retention_days} — مدت اعتبار ضبط. DELETE — حذف فوری ضبط از سرور."""

    def patch(self, request, pk):
        r = get_object_or_404(ClassRecording.objects.select_related("session__course__org", "session__course__teacher"), pk=pk)
        if not _can_manage(request.user, r.session):
            raise PermissionDenied("مدت اعتبار را دبیر یا مسئول مدرسه تعیین می‌کند.")
        try:
            days = int(request.data.get("retention_days"))
        except (TypeError, ValueError):
            raise serializers.ValidationError({"retention_days": "تعداد روز را عدد بنویسید."})
        if not 1 <= days <= 365:
            raise serializers.ValidationError({"retention_days": "مدت اعتبار باید بین ۱ تا ۳۶۵ روز باشد."})
        if r.status != "active":
            raise serializers.ValidationError({"retention_days": "این ضبط قبلاً حذف شده است."})
        R.set_retention(r, days)
        audit(request, "live.recording.retention", f"rec:{r.pk} days:{days}")
        return Response(_rec_row(r))

    def delete(self, request, pk):
        r = get_object_or_404(ClassRecording.objects.select_related("session__system", "session__course__org", "session__course__teacher"), pk=pk)
        if not _can_manage(request.user, r.session):
            raise PermissionDenied("ضبط را دبیر یا مسئول مدرسه حذف می‌کند.")
        if r.status == "active":
            try:
                R.delete_remote(r)
            except Exception:
                return Response({"ok": False, "errors": ["حذف از سرور انجام نشد؛ دوباره امتحان کنید."]}, status=503)
        audit(request, "live.recording.delete", f"rec:{r.pk}")
        return Response(status=204)


class AttendanceExcelView(APIView):
    """GET /api/live/<pk>/attendance.xlsx — گزارش حضور و غیاب جلسه به‌صورت Excel."""

    def get(self, request, pk):
        from django.http import HttpResponse
        from . import attendance_report as AR
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher"), pk=pk)
        if not _can_manage(request.user, s):
            raise PermissionDenied("گزارش حضور را دبیر همان درس یا مسئول مدرسه می‌بیند.")
        data = AR.render_xlsx(s, AR.build(s))
        audit(request, "live.attendance.xlsx", f"session:{s.pk}")
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="attendance-{s.pk}.xlsx"'
        return resp


class AttendancePrintLinkView(APIView):
    """GET /api/live/<pk>/attendance/print-link/ — پیوند کوتاه‌مدت چاپ گزارش حضور (تب جدید، PDF با Ctrl+P)."""

    def get(self, request, pk):
        from django.core import signing
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher"), pk=pk)
        if not _can_manage(request.user, s):
            raise PermissionDenied("گزارش حضور را دبیر همان درس یا مسئول مدرسه می‌بیند.")
        token = signing.dumps({"s": s.pk}, salt="live-attendance")
        audit(request, "live.attendance.print", f"session:{s.pk}")
        return Response({"url": f"/api/live-attendance-print/{token}/"})


class AttendancePublicPrintView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        from django.core import signing
        from django.http import HttpResponse
        from . import attendance_report as AR
        try:
            p = signing.loads(token, salt="live-attendance", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی جلسه چاپ کنید.", status=403, content_type="text/plain; charset=utf-8")
        s = get_object_or_404(ClassSession.objects.select_related("course__org", "course__teacher"), pk=p["s"])
        resp = HttpResponse(AR.render_html(s, AR.build(s)), content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp
