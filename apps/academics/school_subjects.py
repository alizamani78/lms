"""سختی و واحد دروس مدرسه: سختی هر درس در مدرسه، و واحد (ساعت هفتگی مصوب) هر درس در هر پایه و رشته.
قابل مشاهده و ویرایش برای مدیر مدرسه، معاون آموزشی و مدیر سیستم."""
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

from .models import GradeWeeklyHours, SchoolSubjectSetting, Subject

ROLES = ("super_admin", "school_admin", "school_deputy_edu")


def _school(request, param):
    school = get_object_or_404(OrgUnit, pk=param or 0, kind="school")
    u = request.user
    if u.role not in ROLES or not (u.role == "super_admin" or org_in_scope(u, school)):
        raise PermissionDenied("سختی و واحد دروس را مدیر یا معاون آموزشی مدرسه تعیین می‌کند.")
    return school


class SchoolSubjectsView(APIView):
    """GET ?school=ID — دروس مدرسه با سختی و واحدهای پایه/رشته؛ PUT — ذخیره‌ی سختی‌ها و واحدها."""

    def get(self, request):
        school = _school(request, request.query_params.get("school"))
        subjects = Subject.objects.filter(pk__in=Course.objects.filter(org=school, subject__isnull=False).values_list("subject_id", flat=True)).order_by("name")
        settings = dict(SchoolSubjectSetting.objects.filter(school=school).values_list("subject_id", "difficulty"))
        # every (grade, field, subject) that this school teaches; a unit already set (from SIDA or the approved list) is read-only
        combos = {}
        for c in Course.objects.filter(org=school, subject__isnull=False, classroom__isnull=False) \
                .select_related("classroom__grade", "classroom__study_field", "subject"):
            key = (c.classroom.grade_id, c.classroom.study_field_id, c.subject_id)
            combos.setdefault(key, (c.classroom.grade, c.classroom.study_field, c.subject))
        existing = {(u.grade_id, u.field_id, u.subject_id): u for u in GradeWeeklyHours.objects.filter(subject_id__in={k[2] for k in combos})}
        units = []
        for (gid, fid, sid), (grade, field, subject) in sorted(combos.items(), key=lambda kv: (kv[1][0].name, kv[1][2].name)):
            u = existing.get((gid, fid, sid)) or existing.get((gid, None, sid))
            units.append({"grade": gid, "grade_name": grade.name, "field": fid, "field_name": field.name if field else "عمومی",
                          "subject": sid, "subject_name": subject.name,
                          "hours": u.hours if u else None, "fixed": bool(u)})
        return Response({
            "subjects": [{"id": s.pk, "name": s.name, "difficulty": settings.get(s.pk, s.difficulty)} for s in subjects],
            "units": units,
        })

    def put(self, request):
        school = _school(request, request.data.get("school"))
        for sid, raw in (request.data.get("difficulties") or {}).items():
            try:
                d = int(raw)
            except (TypeError, ValueError):
                raise serializers.ValidationError({"difficulties": "سختی باید عدد ۱ تا ۵ باشد."})
            if not 1 <= d <= 5:
                raise serializers.ValidationError({"difficulties": "سختی باید بین ۱ (آسان) و ۵ (سخت) باشد."})
            SchoolSubjectSetting.objects.update_or_create(school=school, subject_id=int(sid), defaults={"difficulty": d})
        for row in request.data.get("units") or []:
            gid, sid = int(row["grade"]), int(row["subject"])
            fid = int(row["field"]) if row.get("field") else None
            if row.get("hours") in (None, ""):
                continue
            try:
                hours = int(row.get("hours"))
            except (TypeError, ValueError):
                raise serializers.ValidationError({"units": "واحد (ساعت هفتگی) باید عدد باشد."})
            if not 0 <= hours <= 40:
                raise serializers.ValidationError({"units": "واحد باید بین ۰ و ۴۰ باشد."})
            # an existing unit is updated; a new one is created (the same subject's units in other grades follow it when they have none)
            # a duplicate row must not stop the save: the first one is updated
            unit = GradeWeeklyHours.objects.filter(grade_id=gid, subject_id=sid, field_id=fid).order_by("pk").first()
            if unit is not None:
                unit.hours = hours
                unit.save(update_fields=["hours"])
            else:
                GradeWeeklyHours.objects.create(grade_id=gid, subject_id=sid, field_id=fid, hours=hours)
            # the same subject (same code) in the school's other grades and fields takes the same unit, where it has none yet
            for c in Course.objects.filter(org=school, subject_id=sid, classroom__isnull=False).select_related("classroom"):
                g2, f2 = c.classroom.grade_id, c.classroom.study_field_id
                if GradeWeeklyHours.objects.filter(grade_id=g2, subject_id=sid, field_id=f2).exists() or \
                        (f2 is not None and GradeWeeklyHours.objects.filter(grade_id=g2, subject_id=sid, field__isnull=True).exists()):
                    continue
                GradeWeeklyHours.objects.create(grade_id=g2, subject_id=sid, field_id=f2, hours=hours)
        audit(request, "academics.subject_settings", f"school:{school.pk}")
        return Response({"ok": True})
