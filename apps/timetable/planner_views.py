"""API برنامه‌ریزی: تولید، نمایش (با رنگ هر درس)، ویرایش با کنترل تداخل، بررسی، تأیید، بازگشت، و کارگاه‌ها و دروس سخت."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, Subject
from apps.accounts.models import OrgUnit
from apps.core.models import audit
from apps.core.scope import org_in_scope

from . import planner
from .models import ClassHardLesson, TimetableVersion, Workshop

PLANNERS = ("super_admin", "school_admin", "school_deputy_edu")   # تولید و ویرایش
APPROVERS = ("super_admin", "school_admin", "school_deputy_edu")  # تأیید


def _school_check(user, school, roles=PLANNERS):
    if user.role not in roles or not org_in_scope(user, school):
        raise PermissionDenied("این کار را مدیر یا معاون آموزشی مدرسه انجام می‌دهد.")


def _version_payload(v):
    entries = []
    for e in v.entries.select_related("classroom__grade", "subject", "teacher", "period", "workshop"):
        entries.append({"id": e.pk, "classroom": e.classroom_id, "classroom_name": f"{e.classroom.grade.name} — {e.classroom.name}",
                        "weekday": e.weekday, "period": e.period.number, "subject": e.subject_id, "subject_name": e.subject.name,
                        "colour": planner.colour_of(e.subject_id), "teacher": e.teacher_id,
                        "teacher_name": (e.teacher.get_full_name() or e.teacher.username) if e.teacher_id else "",
                        "workshop": e.workshop_id})
    return {"id": v.pk, "title": v.title, "status": v.status, "status_label": v.get_status_display(),
            "note": v.note, "entries": entries}


class GenerateView(APIView):
    """POST {school, title} — ساخت پیش‌نویس برنامه‌ی تمام کلاس‌ها. اگر محدودیتی برآورده نشود، علت را برمی‌گرداند."""

    def post(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _school_check(request.user, school)
        res = planner.generate(school, request.user, title=str(request.data.get("title") or "")[:150],
                               thursday=str(request.data.get("thursday", "")).lower() in ("1", "true", "yes", "on"))
        if not res["ok"]:
            return Response({"ok": False, "errors": res["errors"]}, status=422)
        audit(request, "timetable.generate", f"school:{school.pk} version:{res['version']}")
        return Response(res, status=201)


class VersionListView(APIView):
    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _school_check(request.user, school, PLANNERS + ("school_deputy_exec", "teacher"))
        rows = TimetableVersion.objects.filter(school=school).order_by("-created_at")[:50]
        return Response([{"id": v.pk, "title": v.title, "status": v.status, "status_label": v.get_status_display(),
                          "created_at": v.created_at.isoformat()} for v in rows])


class VersionView(APIView):
    def get(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school, PLANNERS + ("school_deputy_exec", "teacher"))
        payload = _version_payload(v)
        payload["problems"] = planner.version_problems(v)
        return Response(payload)


class EntryView(APIView):
    """POST {entry_id?, classroom, weekday, period, subject, teacher?, workshop?} — افزودن یا جابه‌جایی یک خانه."""

    def post(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school)
        ok, errs, entry = planner.edit_entry(v, request.data)
        if not ok:
            return Response({"ok": False, "errors": errs}, status=422)
        audit(request, "timetable.edit", f"version:{v.pk} entry:{entry.pk}")
        return Response({"ok": True, "entry": entry.pk}, status=201)

    def delete(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school)
        if v.status not in (TimetableVersion.DRAFT, TimetableVersion.REVIEW):
            raise serializers.ValidationError({"detail": "فقط نسخه‌ی پیش‌نویس یا در انتظار بررسی قابل ویرایش است."})
        v.entries.filter(pk=request.data.get("entry_id") or 0).delete()
        return Response(status=204)


class SubmitView(APIView):
    def post(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school)
        ok, problems = planner.submit(v)
        if not ok:
            return Response({"ok": False, "errors": problems}, status=422)
        return Response({"ok": True, "status": v.status})


class ApproveView(APIView):
    def post(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school, APPROVERS)
        ok, problems = planner.approve(v, request.user)
        if not ok:
            return Response({"ok": False, "errors": problems}, status=422)
        audit(request, "timetable.approve", f"version:{v.pk}")
        return Response({"ok": True, "status": v.status})


class RejectView(APIView):
    def post(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school, APPROVERS)
        ok, errs = planner.reject(v, request.data.get("note"))
        if not ok:
            return Response({"ok": False, "errors": errs}, status=422)
        return Response({"ok": True, "status": v.status})


class RegenerateView(APIView):
    def post(self, request, pk):
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school)
        res = planner.regenerate(v, request.user)
        if not res["ok"]:
            return Response({"ok": False, "errors": res["errors"]}, status=422)
        return Response(res, status=201)


class WorkshopView(APIView):
    """GET ?school=ID — کارگاه‌ها و رشته‌هایشان. POST {school, name, capacity, fields:[ids]} — افزودن کارگاه."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _school_check(request.user, school)
        return Response([{"id": w.pk, "name": w.name, "capacity": w.capacity,
                          "fields": [f.pk for f in w.fields.all()], "field_names": [f.name for f in w.fields.all()]}
                         for w in Workshop.objects.filter(school=school).prefetch_related("fields")])

    def post(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _school_check(request.user, school)
        name = str(request.data.get("name") or "").strip()
        if not name:
            raise serializers.ValidationError({"name": "نام کارگاه را وارد کنید."})
        cap = int(request.data.get("capacity") or 1)
        if not 1 <= cap <= 20:
            raise serializers.ValidationError({"capacity": "تعداد کلاس هم‌زمان باید بین ۱ و ۲۰ باشد."})
        w, created = Workshop.objects.get_or_create(school=school, name=name, defaults={"capacity": cap})
        if not created:
            raise serializers.ValidationError({"name": "کارگاهی با این نام در این مدرسه از قبل هست (تکراری)."})
        from apps.academics.models import StudyField
        w.fields.set(StudyField.objects.filter(pk__in=request.data.get("fields") or []))
        return Response({"id": w.pk}, status=201)


class HardLessonsView(APIView):
    """GET ?classroom=ID — دروس سخت کلاس. PUT {classroom, subjects:[ids]} — ثبت دروس سخت (پیش از برنامه‌ریزی)."""

    def get(self, request):
        cls = get_object_or_404(ClassRoom.objects.select_related("school"), pk=request.query_params.get("classroom") or 0)
        _school_check(request.user, cls.school)
        return Response(list(ClassHardLesson.objects.filter(classroom=cls).values_list("subject_id", flat=True)))

    def put(self, request):
        cls = get_object_or_404(ClassRoom.objects.select_related("school"), pk=request.data.get("classroom") or 0)
        _school_check(request.user, cls.school)
        ids = {int(x) for x in (request.data.get("subjects") or []) if str(x).isdigit()}
        ClassHardLesson.objects.filter(classroom=cls).exclude(subject_id__in=ids).delete()
        for sid in ids:
            ClassHardLesson.objects.get_or_create(classroom=cls, subject=get_object_or_404(Subject, pk=sid))
        return Response({"ok": True, "count": len(ids)})


class ExportView(APIView):
    """GET ?format=xlsx|pdf&classroom=ID&teacher=ID — خروجی برنامه‌ی یک نسخه (کل مدرسه، یک کلاس یا یک دبیر)."""

    def get(self, request, pk):
        from django.http import HttpResponse
        from . import exports
        v = get_object_or_404(TimetableVersion.objects.select_related("school"), pk=pk)
        _school_check(request.user, v.school, PLANNERS + ("school_deputy_exec", "teacher"))
        cls = request.query_params.get("classroom") or None
        tch = request.query_params.get("teacher") or None
        fmt = (request.query_params.get("format") or "xlsx").lower()
        suffix = f"-class{cls}" if cls else (f"-teacher{tch}" if tch else "")
        try:
            if fmt == "pdf":
                data, ctype, ext = exports.export_pdf(v, cls, tch), "application/pdf", "pdf"
            else:
                data, ctype, ext = exports.export_excel(v, cls, tch), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
        except FileNotFoundError as e:
            return Response({"ok": False, "errors": [str(e)]}, status=422)
        audit(request, "timetable.export", f"version:{v.pk} {fmt}{suffix}")
        resp = HttpResponse(data, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="timetable-{v.pk}{suffix}.{ext}"'
        return resp
