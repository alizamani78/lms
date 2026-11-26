"""ویرایش دستی برنامه‌ی هفتگی: افزودن درس به یک زنگ، و حذف یک جلسه.
فقط روی نسخه‌ی پیش‌نویس یا در انتظار بررسی؛ نسخه‌ی تأییدشده قفل است.
قواعد: زنگ گنجایش ساعت دارد؛ دبیر در یک زنگ فقط در یک کلاس؛ ساعت هر درس بیش از لازم ثبت نمی‌شود.
هشدار (نه خطا): دبیر از سقف روز یا ساعت حضورش فراتر رود."""
from collections import defaultdict
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, GradeWeeklyHours
from apps.courses.models import Course
from apps.core.scope import org_in_scope

from .hours import bell_hours
from .models import BellPeriod, TimetableEntry, TimetableVersion
from .solver_weighted import max_days_for

EDITABLE = ("draft", "review")


def _check_version(request, ver):
    u = request.user
    if u.role not in ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec"):
        raise PermissionDenied("ویرایش برنامه را مدیر یا معاون آموزشی انجام می‌دهد.")
    if u.role != "super_admin" and not org_in_scope(u, ver.school):
        raise PermissionDenied("این برنامه در کارتابل شما نیست.")
    if ver.status not in EDITABLE:
        raise serializers.ValidationError({"status": "این نسخه تأیید شده و قفل است؛ برای تغییر، یک نسخه‌ی پیش‌نویس تازه بسازید."})


class TimetableEntryView(APIView):
    """POST {version, classroom, weekday, period, subject, piece_hours?} — افزودن درس به زنگ.
    DELETE /api/timetable/entry/<pk>/ — حذف یک جلسه از برنامه."""

    def post(self, request):
        ver = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=request.data.get("version") or 0)
        _check_version(request, ver)
        from .models import snapshot
        before_state = snapshot(ver)
        cls = get_object_or_404(ClassRoom, pk=request.data.get("classroom") or 0, school=ver.school)
        wd = int(request.data.get("weekday", -1))
        bell = get_object_or_404(BellPeriod, school=ver.school, number=int(request.data.get("period") or 0))
        course = get_object_or_404(Course, classroom=cls, subject_id=int(request.data.get("subject") or 0))
        slot_pieces = list(TimetableEntry.objects.filter(version=ver, classroom=cls, weekday=wd, period=bell))
        bell_hrs = bell.hours
        used = sum(p.piece_hours or bell_hrs for p in slot_pieces)
        if len(slot_pieces) >= 2:
            raise serializers.ValidationError({"period": "این زنگ پر است (دو درس در آن هست)."})
        # the lesson's weekly requirement and what is already placed
        gw = GradeWeeklyHours.objects.filter(grade=cls.grade, subject=course.subject, field=cls.study_field).first() \
            or GradeWeeklyHours.objects.filter(grade=cls.grade, subject=course.subject, field__isnull=True).first()
        need = gw.hours if gw else (course.subject.weekly_hours or 0)
        placed = sum(e.piece_hours or 1 for e in TimetableEntry.objects.filter(version=ver, classroom=cls, subject=course.subject))
        room = min(bell_hrs - used, need - placed) if need else bell_hrs - used
        if room <= 0:
            raise serializers.ValidationError({"period": "ساعتی از این درس برای جا دادن باقی نمانده یا این زنگ پر است."})
        size = int(request.data.get("piece_hours") or 0) or room
        if used + size > bell_hrs:
            raise serializers.ValidationError({"period": f"ساعت این زنگ {bell_hrs} است و {used} ساعت پر شده؛ برای {size} ساعت جا نیست."})
        if need and placed + size > need:
            raise serializers.ValidationError({"subject": f"این درس {need} ساعت لازم دارد و {placed} ساعت جا شده؛ {size} ساعت دیگر بیش از لازم است."})
        # the teacher's presence hours: if the teacher has set presence, this bell must be one of them
        from apps.timetable.models import TeacherAvailability
        if course.teacher_id and TeacherAvailability.objects.filter(teacher=course.teacher, period__school=ver.school).exists():
            if not TeacherAvailability.objects.filter(teacher=course.teacher, period=bell, weekday=wd).exists():
                raise serializers.ValidationError({"teacher": "دبیر این درس در این زنگ ساعت حضور ندارد؛ زنگ دیگری را انتخاب کنید."})
        # the teacher is in one class per bell
        teacher_clash = TimetableEntry.objects.filter(version=ver, weekday=wd, period=bell, teacher=course.teacher).exclude(classroom=cls) if course.teacher_id else TimetableEntry.objects.none()
        if teacher_clash.exists():
            other = teacher_clash.first().classroom.name
            raise serializers.ValidationError({"teacher": f"دبیر این درس در همین زنگ در کلاس «{other}» تدریس دارد."})
        part = 1 if any(p.part == 0 for p in slot_pieces) else 0
        if any(p.part == part for p in slot_pieces):
            raise serializers.ValidationError({"period": "نیمه‌ی این زنگ پر است."})
        warnings = []
        if course.teacher_id:
            days = set(TimetableEntry.objects.filter(version=ver, teacher=course.teacher).values_list("weekday", flat=True))
            total = sum(int(x) for x in TimetableEntry.objects.filter(version=ver, teacher=course.teacher).values_list("piece_hours", flat=True)) + size
            if wd not in days and len(days) + 1 > max_days_for(total):
                warnings.append(f"این دبیر از سقف روزهای تدریس ({max_days_for(total)} روز) فراتر می‌رود.")
        e = TimetableEntry.objects.create(version=ver, classroom=cls, weekday=wd, period=bell, subject=course.subject,
                                          teacher=course.teacher, part=part, piece_hours=size,
                                          extra_day=any("سقف روزهای" in w for w in warnings))
        from .models import log_change
        log_change(ver, request.user, "add", f"افزودن {course.subject.name} به کلاس {cls.name}، روز {wd}، زنگ {bell.number}", before=before_state)
        return Response({"ok": True, "id": e.pk, "warnings": warnings})

    def delete(self, request, pk):
        e = get_object_or_404(TimetableEntry.objects.select_related("version", "version__school"), pk=pk)
        _check_version(request, e.version)
        from .models import log_change, snapshot
        before_state = snapshot(e.version)
        subj = e.subject.name if e.subject_id else "—"
        log_change(e.version, request.user, "delete", f"حذف {subj} از کلاس {e.classroom.name}، روز {e.weekday}، زنگ {e.period.number}", before=before_state)
        e.delete()
        return Response({"ok": True})


class RetryMissingView(APIView):
    """POST {version} — تلاش دوباره برای دروس جانگرفته‌ی یک پیش‌نویس (جلسه‌های موجود نمی‌شوند)."""

    def post(self, request):
        from .planner import retry_missing
        ver = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=request.data.get("version") or 0)
        _check_version(request, ver)
        from .models import log_change, snapshot
        before_state = snapshot(ver)
        res = retry_missing(ver, ver.school)
        log_change(ver, request.user, "retry_by_user", f"تلاش دوباره: {res.get('added', 0)} جلسه", before=before_state)
        if not res.get("ok"):
            raise serializers.ValidationError({"detail": " ".join(res.get("errors", []))})
        return Response(res)


class TimetableHistoryView(APIView):
    """GET ?version=ID — تاریخچه‌ی تغییرات یک نسخه (زمان، کاربر، عملیات، شرح)."""

    def get(self, request):
        from .models import TimetableChange
        ver = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=request.query_params.get("version") or 0)
        if request.user.role not in ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec"):
            raise PermissionDenied("تاریخچه‌ی برنامه را مدیر یا معاون می‌بیند.")
        rows = TimetableChange.objects.filter(version=ver).select_related("user")[:300]
        return Response([{"id": r.pk, "at": r.at.strftime("%Y-%m-%d %H:%M"), "user": (r.user.get_full_name() or r.user.username) if r.user_id else "سامانه",
                          "action": r.action, "detail": r.detail, "restorable": r.before is not None} for r in rows])


class TimetableRestoreView(APIView):
    """POST {change} — بازگرداندن نسخه به وضعیتی که قبل از همان تغییر بود (فقط برای نسخه‌ی پیش‌نویس/در انتظار)."""

    def post(self, request):
        from .models import TimetableChange, TimetableEntry, log_change, snapshot
        ch = get_object_or_404(TimetableChange.objects.select_related("version", "version__school"), pk=request.data.get("change") or 0)
        ver = ch.version
        _check_version(request, ver)
        if ch.before is None:
            raise serializers.ValidationError({"change": "برای این تغییر وضعیت قبلی ثبت نشده است."})
        current = snapshot(ver)
        TimetableEntry.objects.filter(version=ver).delete()
        TimetableEntry.objects.bulk_create([TimetableEntry(version=ver, classroom_id=r["classroom"], weekday=r["weekday"],
                                                           period_id=r["period"], subject_id=r["subject"], teacher_id=r["teacher"],
                                                           workshop_id=r["workshop"], part=r["part"], piece_hours=r["piece_hours"],
                                                           extra_day=r["extra_day"]) for r in ch.before])
        log_change(ver, request.user, "restore", f"بازگرداندن به پیش از: {ch.detail}", before=current)
        return Response({"ok": True, "entries": len(ch.before)})



class TimetableCompareView(APIView):
    """GET ?version=ID&with=ID — مقایسه‌ی دو نسخه: جلسه‌هایی که فقط در یکی هستند (اضافه یا حذف شده‌اند).
    بدون with، نسخه‌ی قبلی همان مدرسه مقایسه می‌شود."""

    def get(self, request):
        ver = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=request.query_params.get("version") or 0)
        if request.user.role not in ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec"):
            raise PermissionDenied("مقایسه‌ی نسخه‌ها را مدیر یا معاون می‌بیند.")
        if (request.query_params.get("with") or "").isdigit():
            other = get_object_or_404(TimetableVersion, pk=int(request.query_params["with"]), school=ver.school)
        else:
            other = TimetableVersion.objects.filter(school=ver.school, pk__lt=ver.pk).order_by("-pk").first()
        if other is None:
            return Response({"version": ver.pk, "with": None, "added": [], "removed": [], "message": "نسخه‌ی دیگری برای مقایسه نیست."})

        def keys(v):
            out = {}
            for e in TimetableEntry.objects.filter(version=v).select_related("classroom", "subject", "period"):
                k = (e.classroom.name, e.weekday, e.period.number, e.subject.name)
                out[k] = out.get(k, 0) + (e.piece_hours or 1)
            return out
        a, b = keys(ver), keys(other)
        added = [{"class": k[0], "day": k[1], "period": k[2], "lesson": k[3], "hours": v} for k, v in a.items() if k not in b]
        removed = [{"class": k[0], "day": k[1], "period": k[2], "lesson": k[3], "hours": v} for k, v in b.items() if k not in a]
        return Response({"version": ver.pk, "with": other.pk, "with_title": other.title, "added": added, "removed": removed})
