"""برنامه‌ی مراقبت از امتحانات: مشاهده برای معلمان؛ طراحی، ویرایش و حذف برای مدیر و معاون آموزشی و مدیر سیستم."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import SchoolMembership, User
from apps.core.jalali import system_date
from apps.core.models import audit
from apps.core.scope import org_in_scope

from .duty import generate, teacher_hours
from .models import ExamDuty, ExamSchedule

MANAGE = ("super_admin", "school_admin", "school_deputy_edu")


def _can_manage(user, school):
    return user.role == "super_admin" or (user.role in MANAGE and org_in_scope(user, school))


class DutyView(APIView):
    """GET — برنامه‌ی مراقبت (برای همه‌ی اعضای مدرسه، فقط خواندنی برای غیرمدیران).
    POST {generate:true} — ساخت خودکار؛ POST {date, teacher} — افزودن مراقب دستی.
    PATCH {invigilators_per_day, hours_per_duty} — تنظیمات؛ DELETE {id} — حذف یک مراقب."""

    def _sched(self, request, pk):
        sch = get_object_or_404(ExamSchedule.objects.select_related("school"), pk=pk)
        m = SchoolMembership.objects.filter(user=request.user, school=sch.school).exists()
        if not (m or request.user.role == "super_admin" or org_in_scope(request.user, sch.school)):
            raise PermissionDenied("برنامه‌ی مراقبت این مدرسه برای شما نیست.")
        return sch

    def get(self, request, pk):
        sch = self._sched(request, pk)
        duties = ExamDuty.objects.filter(schedule=sch).select_related("teacher").order_by("date", "id")
        if request.user.role == "teacher" and not _can_manage(request.user, sch.school):
            # a teacher sees only his own invigilation days, and only after the plan is approved
            if sch.status != "approved":
                duties = duties.none()
            else:
                duties = duties.filter(teacher=request.user)
        hours = teacher_hours(sch.school)
        limited = request.user.role == "teacher" and not _can_manage(request.user, sch.school)
        if limited:
            # the teacher sees only his own days and hours: no other teachers' names, hours or notes
            hours = {request.user.pk: hours.get(request.user.pk, 0)}
        return Response({
            "limited": limited,
            "editable": _can_manage(request.user, sch.school),
            "invigilators_per_day": sch.invigilators_per_day, "hours_per_duty": sch.hours_per_duty,
            "duty_notes": sch.duty_notes,
            "schedule": {"id": sch.pk, "title": sch.title, "status": sch.status},
            "duties": [{"id": d.pk, "date": d.date.isoformat(), "date_fa": system_date(d.date), "teacher": d.teacher_id,
                        "teacher_name": d.teacher.get_full_name() or d.teacher.username,
                        "national": d.teacher.national_id or "", "by_subject": d.by_subject, "note": d.note,
                        "hours": hours.get(d.teacher_id, 0)} for d in duties],
            "teachers": ([] if limited else [{"id": t.pk, "name": t.get_full_name() or t.username, "national": t.national_id or "",
                          "hours": hours.get(t.pk, 0)}
                         for t in User.objects.filter(pk__in=list(hours.keys()), role="teacher").order_by("last_name", "first_name")]),
        })

    def post(self, request, pk):
        sch = self._sched(request, pk)
        if not _can_manage(request.user, sch.school):
            raise PermissionDenied("برنامه‌ی مراقبت را مدیر یا معاون آموزشی طراحی می‌کند.")
        if request.data.get("generate"):
            made, warnings = generate(sch)
            audit(request, "exam.duty.generate", f"schedule:{sch.pk} made:{made}")
            return Response({"ok": True, "made": made, "warnings": warnings})
        try:
            day = __import__("datetime").date.fromisoformat(str(request.data.get("date")))
            teacher = User.objects.get(pk=request.data.get("teacher"), role="teacher")
        except (ValueError, TypeError, User.DoesNotExist):
            raise serializers.ValidationError({"teacher": "روز و معلم را درست انتخاب کنید."})
        duty, created = ExamDuty.objects.get_or_create(schedule=sch, date=day, teacher=teacher)
        audit(request, "exam.duty.add", f"schedule:{sch.pk} teacher:{teacher.pk}")
        return Response({"ok": True, "created": created})

    def patch(self, request, pk):
        sch = self._sched(request, pk)
        if not _can_manage(request.user, sch.school):
            raise PermissionDenied("تنظیمات مراقبت را مدیر یا معاون آموزشی تعیین می‌کند.")
        if "note" in request.data and request.data.get("id"):
            duty = get_object_or_404(ExamDuty, pk=request.data["id"], schedule=sch)
            duty.note = str(request.data.get("note") or "")[:500]
            duty.save(update_fields=["note"])
            audit(request, "exam.duty.note", f"duty:{duty.pk}")
            return Response({"ok": True})
        if "duty_notes" in request.data:
            sch.duty_notes = str(request.data.get("duty_notes") or "")[:3000]
            sch.save(update_fields=["duty_notes"])
            return Response({"ok": True})
        if "invigilators_per_day" in request.data:
            sch.invigilators_per_day = max(1, min(20, int(request.data["invigilators_per_day"])))
        if "hours_per_duty" in request.data:
            sch.hours_per_duty = max(1, min(60, int(request.data["hours_per_duty"])))
        sch.save(update_fields=["invigilators_per_day", "hours_per_duty"])
        return Response({"ok": True})

    def delete(self, request, pk):
        sch = self._sched(request, pk)
        if not _can_manage(request.user, sch.school):
            raise PermissionDenied("حذف مراقب را مدیر یا معاون آموزشی انجام می‌دهد.")
        ExamDuty.objects.filter(schedule=sch, pk=request.data.get("id") or 0).delete()
        return Response(status=204)
