"""برنامه‌ی امتحانی: ساخت پیشنهادی، ویرایش دستی با کنترل، ارسال و تأیید، و نمایش برای دبیر و دانش‌آموز."""
import datetime as dt

from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, Subject
from apps.accounts.models import OrgUnit
from apps.core.models import audit
from apps.core.scope import org_in_scope

from . import schedule as S
from apps.core.jalali import format_jalali, gregorian_to_jalali, jalali_to_gregorian

_FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "00123456789001234567")


def parse_day(text):
    """تاریخ از کاربر: شمسی (۱۴۰۵/۰۸/۲۳ یا ۱۴۰۵-۰۸-۲۳) یا میلادی (2026-11-14)."""
    t = str(text or "").strip().translate(_FA_TO_EN)
    if not t:
        raise ValueError("تاریخ خالی است.")
    parts = t.replace("-", "/").split("/")
    if len(parts) != 3:
        raise ValueError("تاریخ را درست وارد کنید.")
    a, b, c = (int(x) for x in parts)
    if a > 1500:           # سال اول: میلادی یا شمسی؟ شمسی‌ها (۱۴۰۰ به بالا) در ایران کوچک‌تر از ۱۶۰۰ نیستند
        if a >= 1600:
            return dt.date(a, b, c)
        gy, gm, gd = jalali_to_gregorian(a, b, c)
        return dt.date(gy, gm, gd)
    if a > 1300:           # سال شمسی
        gy, gm, gd = jalali_to_gregorian(a, b, c)
        return dt.date(gy, gm, gd)
    return dt.date(a, b, c)


def fa_day(d):
    return format_jalali(d, with_time=False)

from .models import ExamSchedule, ExamScheduleEntry, ExamVenue

EDIT = ("super_admin", "school_admin", "school_deputy_edu")


def _check(user, school, roles=EDIT):
    if user.role not in roles or not org_in_scope(user, school):
        raise PermissionDenied("برنامه‌ی امتحانی را مدیر یا معاون آموزشی مدرسه تنظیم و تأیید می‌کند.")


def _entry(e):
    return {"id": e.pk, "classroom": e.classroom_id, "classroom_name": f"{e.classroom.grade.name} — {e.classroom.name}",
            "subject": e.subject_id, "subject_name": e.subject.name, "date": e.date.isoformat(), "date_fa": fa_day(e.date),
            "start_time": e.start_time.strftime("%H:%M"), "duration": e.duration_minutes,
            "venue_name": e.venue_name, "venue_address": e.venue_address, "difficulty": e.subject.difficulty,
            # the exam made for this row: the manager sees it, edits it and publishes it from the schedule
            "exam": e.exam_id, "exam_published": bool(e.exam and e.exam.is_published) if e.exam_id else None}


def parse_shifts(text):
    """«08:00-10:00, 10:00-12:00» → [(08:00, 120 دقیقه), (10:00, 120 دقیقه)]؛ خالی یعنی یک نوبت پیش‌فرض."""
    if isinstance(text, list):
        return [(dt.time.fromisoformat(x["start"]), int(x["duration"])) for x in text]
    out = []
    for part in str(text or "").replace("،", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            a, b = [x.strip() for x in part.split("-")]
            t1, t2 = dt.time.fromisoformat(a), dt.time.fromisoformat(b)
        except ValueError:
            raise serializers.ValidationError({"shifts": f"نوبت «{part}» درست نیست؛ مثلاً 08:00-10:00 بنویسید."})
        mins = (t2.hour * 60 + t2.minute) - (t1.hour * 60 + t1.minute)
        if mins <= 0:
            raise serializers.ValidationError({"shifts": f"پایان نوبت «{part}» باید بعد از شروع باشد."})
        out.append((t1, mins))
    return out


class ScheduleListView(APIView):
    """GET ?school=ID — برنامه‌های امتحانی مدرسه. POST {school, title, start, end, time, duration, venue_id} — ساخت پیشنهاد."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school, EDIT + ("school_deputy_exec", "teacher"))
        rows = ExamSchedule.objects.filter(school=school).order_by("-created_at")[:30]
        return Response([{"id": s.pk, "title": s.title, "status": s.status, "start": s.start_date.isoformat(),
                           "end": s.end_date.isoformat(), "start_fa": fa_day(s.start_date), "end_fa": fa_day(s.end_date),
                          "shifts": s.shifts or [], "printed": bool(s.printed_at)} for s in rows])

    def post(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _check(request.user, school)
        try:
            start = parse_day(request.data["start"])
            end = parse_day(request.data["end"])
        except (KeyError, ValueError):
            raise serializers.ValidationError({"start": "بازه‌ی تاریخ را درست وارد کنید."})
        if end < start:
            raise serializers.ValidationError({"end": "تاریخ پایان باید بعد از شروع باشد."})
        title = str(request.data.get("title") or "برنامه‌ی امتحانی").strip()[:200]
        t = dt.time.fromisoformat(request.data.get("time") or "08:00")
        duration = int(request.data.get("duration") or 90)
        shifts = parse_shifts(request.data.get("shifts")) or None
        off_list = [str(x) for x in (request.data.get("off_days") or []) if str(x) in ("3", "4")]
        off = S.parse_off(",".join(off_list))
        # قواعد انتخاب‌شده توسط مدیر: روزهای استراحت بر اساس سختی، پایه‌های انتخاب‌شده، و نوبت هر پایه
        rest_map = {}
        for k, v in dict(request.data.get("rest") or {}).items():
            if str(k).isdigit() and str(v).strip().isdigit():
                rest_map[int(k)] = int(v)
        grade_ids = [int(x) for x in (request.data.get("grades") or []) if str(x).isdigit()]
        classes = None
        if grade_ids:
            from apps.academics.models import ClassRoom
            classes = list(ClassRoom.objects.filter(school=school, academic_year__is_current=True,
                                                    grade_id__in=grade_ids).values_list("pk", flat=True))
        grade_shift = {}
        for gid, txt in dict(request.data.get("grade_shift") or {}).items():
            if str(gid).isdigit() and str(txt).strip():
                one = parse_shifts(txt)
                if one:
                    grade_shift[int(gid)] = one[0]
        rows, errors = S.plan(school, start, end, t, duration, per_day_limit=int(request.data.get("per_day") or 6), off=off,
                              shifts=shifts, rest_map=rest_map, grade_shift=grade_shift, classes=classes)
        if not rows and not errors:
            from apps.academics.models import ClassRoom
            from apps.courses.models import Course
            n_cls = ClassRoom.objects.filter(school=school, academic_year__is_current=True).count()
            n_crs = Course.objects.filter(classroom__school=school, classroom__academic_year__is_current=True, subject__isnull=False).count()
            errors = [f"هیچ امتحانی ساخته نشد. کلاس‌های سال جاری: {n_cls}، درس‌های کلاس‌ها: {n_crs}. اگر یکی صفر است، آن را در بخش کلاس‌ها و درس‌ها کامل کنید؛ در غیر این صورت بازه‌ی تاریخ را بزرگ‌تر بگیرید."]
        venue = ExamVenue.objects.filter(pk=request.data.get("venue_id") or 0, school=school).first()
        typed_address = str(request.data.get("venue_address") or "").strip()[:500]
        if venue:
            for r in rows:
                r["venue_name"] = venue.name
                r["venue_address"] = typed_address or venue.address
        elif typed_address:
            for r in rows:
                r["venue_address"] = typed_address
        sch = ExamSchedule.objects.create(school=school, title=title, start_date=start, end_date=end, created_by=request.user,
                                          shifts=[{"start": a.strftime("%H:%M"), "duration": m} for a, m in (shifts or [(t, duration)])],
                                          off_days=",".join(str(x) for x in off))
        S.save_plan(sch, rows)
        audit(request, "exam.schedule.generate", f"school:{school.pk} schedule:{sch.pk}")
        return Response({"id": sch.pk, "entries": len(rows), "errors": errors}, status=201)


class SchedulePrintedView(APIView):
    """POST /api/exam-schedule/<pk>/printed/ — ثبت چاپ برنامه؛ بعد از آن برنامه قابل حذف نیست."""

    def post(self, request, pk):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school, EDIT + ("school_deputy_exec",))
        if sch.status != ExamSchedule.APPROVED:
            raise serializers.ValidationError({"status": "فقط برنامه‌ی تأییدشده را می‌توان چاپ ثبت کرد."})
        if not sch.printed_at:
            sch.printed_at = timezone.now()
            sch.save(update_fields=["printed_at"])
        audit(request, "exam.schedule.printed", f"schedule:{pk}")
        return Response({"ok": True, "printed_at": sch.printed_at.isoformat()})


class ScheduleView(APIView):
    """GET — برنامه با امتحان‌ها. PATCH {title} — عنوان."""

    def delete(self, request, pk):
        """حذف برنامه‌ی امتحانی (پیش‌نویس یا در انتظار تأیید). برنامه‌ی تأییدشده حذف نمی‌شود."""
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school)
        # an approved or printed schedule is deleted only by the general administrator / system administrator
        if (sch.status == ExamSchedule.APPROVED or sch.printed_at) and request.user.role != "super_admin":
            raise serializers.ValidationError({"status": "برنامه‌ی تأییدشده یا چاپ‌شده را فقط مدیر کل یا مدیر سیستم حذف می‌کند."})
        sch.delete()
        audit(request, "exam.schedule.delete", f"schedule:{pk}")
        return Response(status=204)

    def get(self, request, pk):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school, EDIT + ("school_deputy_exec", "teacher"))
        entries = sch.entries.select_related("classroom__grade", "subject").order_by("date", "classroom__grade__order", "classroom__name")
        return Response({"id": sch.pk, "title": sch.title, "status": sch.status, "start": sch.start_date.isoformat(),
                         "end": sch.end_date.isoformat(), "start_fa": fa_day(sch.start_date), "end_fa": fa_day(sch.end_date),
                         "entries": [_entry(e) for e in entries]})

    def patch(self, request, pk):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school)
        if "notes" in request.data:
            sch.notes = str(request.data.get("notes") or "")[:3000]
            sch.save(update_fields=["notes"])
        if sch.status not in (ExamSchedule.DRAFT, ExamSchedule.REVIEW):
            if set(request.data.keys()) - {"notes"}:
                raise serializers.ValidationError({"detail": "برنامه‌ی تأییدشده قابل تغییر نیست؛ فقط تذکرات کارت را می‌توان ویرایش کرد."})
            return Response({"ok": True})
        if request.data.get("title"):
            sch.title = str(request.data["title"]).strip()[:200]
            sch.save(update_fields=["title"])
        return Response({"id": sch.pk, "title": sch.title})


class ScheduleEntryView(APIView):
    """POST {schedule, classroom, subject, date, time, duration, venue_name, venue_address, entry_id?} — افزودن یا ویرایش با کنترل."""

    def post(self, request):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=request.data.get("schedule") or 0)
        _check(request.user, sch.school)
        if sch.status not in (ExamSchedule.DRAFT, ExamSchedule.REVIEW):
            raise serializers.ValidationError({"detail": "برنامه‌ی تأییدشده قابل تغییر نیست."})
        cls = get_object_or_404(ClassRoom, pk=request.data.get("classroom") or 0, school=sch.school)
        sub = get_object_or_404(Subject, pk=request.data.get("subject") or 0)
        try:
            day = parse_day(request.data["date"])
            t = dt.time.fromisoformat(request.data.get("time") or "08:00")
        except (KeyError, ValueError):
            raise serializers.ValidationError({"date": "تاریخ و ساعت را درست وارد کنید."})
        if not (sch.start_date <= day <= sch.end_date):
            raise serializers.ValidationError({"date": "تاریخ خارج از بازه‌ی برنامه است."})
        entry_id = request.data.get("entry_id")
        errs = S.check_entry(sch, cls.pk, sub.pk, day, exclude_pk=entry_id)
        if errs:
            return Response({"ok": False, "errors": errs}, status=422)
        if entry_id:
            e = get_object_or_404(ExamScheduleEntry, pk=entry_id, schedule=sch)
        else:
            e = ExamScheduleEntry(schedule=sch)
        e.classroom, e.subject, e.date, e.start_time = cls, sub, day, t
        e.duration_minutes = int(request.data.get("duration") or 90)
        e.venue_name = str(request.data.get("venue_name") or "")[:150]
        e.venue_address = str(request.data.get("venue_address") or "")
        e.save()
        return Response({"ok": True, "id": e.pk}, status=201)

    def delete(self, request):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=request.data.get("schedule") or 0)
        _check(request.user, sch.school)
        if sch.status not in (ExamSchedule.DRAFT, ExamSchedule.REVIEW):
            raise serializers.ValidationError({"detail": "برنامه‌ی تأییدشده قابل تغییر نیست."})
        sch.entries.filter(pk=request.data.get("entry_id") or 0).delete()
        return Response(status=204)


class ScheduleSubmitView(APIView):
    def post(self, request, pk):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school)
        if sch.status != ExamSchedule.DRAFT:
            raise serializers.ValidationError({"detail": "فقط پیش‌نویس برای تأیید فرستاده می‌شود."})
        sch.status = ExamSchedule.REVIEW
        sch.save(update_fields=["status"])
        return Response({"ok": True, "status": sch.status})


class ScheduleApproveView(APIView):
    """تأیید (مدیر یا معاون آموزشی). بازگرداندن برای اصلاح هم با reject=1."""

    def post(self, request, pk):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school)
        if sch.status != ExamSchedule.REVIEW:
            raise serializers.ValidationError({"detail": "فقط برنامه‌ی در انتظار تأیید قابل تأیید است."})
        if request.data.get("reject"):
            sch.status = ExamSchedule.DRAFT
            sch.save(update_fields=["status"])
            return Response({"ok": True, "status": sch.status})
        S.approve(sch, request.user)
        audit(request, "exam.schedule.approve", f"schedule:{sch.pk}")
        return Response({"ok": True, "status": sch.status})


class MyScheduleView(APIView):
    """GET — امتحان‌های تأییدشده‌ی کلاس‌های دانش‌آموز یا دبیر (کارتابل و پروفایل)."""

    def get(self, request):
        from django.db.models import Q
        u = request.user
        if u.role == "student":
            cls_ids = list(u.classrooms.values_list("pk", flat=True))
            qs = ExamScheduleEntry.objects.filter(schedule__status=ExamSchedule.APPROVED, classroom_id__in=cls_ids)
        elif u.role == "teacher":
            from apps.courses.models import Course
            cls_ids = list(Course.objects.filter(teacher=u, classroom__isnull=False).values_list("classroom_id", flat=True))
            qs = ExamScheduleEntry.objects.filter(schedule__status=ExamSchedule.APPROVED).filter(classroom_id__in=cls_ids)
        else:
            raise PermissionDenied("این بخش برای دبیر و دانش‌آموز است.")
        qs = qs.select_related("classroom__grade", "subject", "schedule").order_by("date", "start_time")
        return Response([dict(_entry(e), schedule=e.schedule.title) for e in qs])


class VenueView(APIView):
    """GET/POST ?school — محل‌های برگزاری امتحان مدرسه (نام و نشانی)."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school, EDIT + ("school_deputy_exec",))
        return Response([{"id": v.pk, "name": v.name, "address": v.address} for v in ExamVenue.objects.filter(school=school)])

    def post(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _check(request.user, school)
        name = str(request.data.get("name") or "").strip()
        if not name:
            raise serializers.ValidationError({"name": "نام محل را وارد کنید."})
        v, created = ExamVenue.objects.get_or_create(school=school, name=name, defaults={"address": str(request.data.get("address") or "")})
        if not created:
            v.address = str(request.data.get("address") or v.address)
            v.save(update_fields=["address"])
        return Response({"id": v.pk}, status=201)


class ScheduleExportView(APIView):
    """GET /api/exam-schedule/<pk>/export.xlsx — برنامه‌ی امتحانی به‌صورت Excel."""

    def get(self, request, pk):
        from django.http import HttpResponse
        from .schedule_export import render_xlsx
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school, EDIT + ("school_deputy_exec",))
        data = render_xlsx(sch)
        audit(request, "exam.schedule.export", f"schedule:{pk}")
        resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="exam-schedule-{pk}.xlsx"'
        return resp


class ScheduleContentPrintLinkView(APIView):
    """GET /api/exam-schedule/<pk>/print-link/ — پیوند کوتاه‌مدت چاپ برنامه (در تب جدید؛ PDF با Ctrl+P)."""

    def get(self, request, pk):
        from django.core import signing
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        _check(request.user, sch.school, EDIT + ("school_deputy_exec",))
        token = signing.dumps({"s": sch.pk}, salt="exam-schedule-print")
        audit(request, "exam.schedule.print", f"schedule:{pk}")
        return Response({"url": f"/api/exam-schedule-print/{token}/"})


class ScheduleContentPublicPrintView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request, token):
        from django.core import signing
        from django.http import HttpResponse
        from .schedule_export import render_html
        try:
            p = signing.loads(token, salt="exam-schedule-print", max_age=900)
        except signing.BadSignature:
            return HttpResponse("این پیوند منقضی شده است؛ دوباره از صفحه‌ی برنامه چاپ کنید.", status=403, content_type="text/plain; charset=utf-8")
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=p["s"])
        resp = HttpResponse(render_html(sch), content_type="text/html; charset=utf-8")
        resp["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'"
        resp["Cache-Control"] = "no-store"
        return resp
