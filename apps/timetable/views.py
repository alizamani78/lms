"""Weekly timetable: school admins (and above, inside their scope) manage it; teachers and students only read theirs."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit, User
from apps.core.jalali import WEEKDAYS
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope, org_scope_q
from apps.courses.models import Course

from .models import BellPeriod, ScheduleEntry

IsAdmin = role_required(*User.ADMIN_ROLES, User.Role.SCHOOL_DEPUTY_EDU)


class TimetableView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


def hm(t):
    return t.strftime("%H:%M")


def _row(e):
    return {"weekday": e.weekday, "weekday_name": WEEKDAYS[e.weekday], "period": e.period.number,
            "start": hm(e.period.start_time), "end": hm(e.period.end_time), "course": e.course.title,
            "teacher": (e.course.teacher.get_full_name() or e.course.teacher.username) if e.course.teacher_id else "",
            "classroom": e.classroom.name, "classroom_id": e.classroom_id, "room": e.room}


class MyScheduleView(TimetableView):
    """GET /api/schedule/[?year=<academic_year_id>&classroom=<id>] — the caller's own weekly program."""

    def get(self, request):
        u, p = request.user, request.query_params
        qs = ScheduleEntry.objects.select_related("classroom", "period", "course__teacher")
        if u.role == User.Role.STUDENT:
            from django.db.models import Q as _Q
            qs = qs.filter(_Q(classroom__students=u) | _Q(course__enrollments__student=u)).distinct()
        elif u.role == User.Role.TEACHER:
            qs = qs.filter(course__teacher=u)
        elif u.role in User.ADMIN_ROLES:
            qs = qs.filter(org_scope_q(u, "classroom__school"))
            if p.get("classroom", "").isdigit():
                qs = qs.filter(classroom_id=int(p["classroom"]))
        else:
            qs = qs.none()
        if p.get("year", "").isdigit():
            qs = qs.filter(classroom__academic_year_id=int(p["year"]))
        else:
            qs = qs.filter(classroom__academic_year__is_current=True)
        rows = [_row(e) for e in qs.order_by("weekday", "period__start_time")[:600]]
        return Response({"weekdays": WEEKDAYS, "entries": rows})


class PeriodIn(serializers.Serializer):
    number = serializers.IntegerField(min_value=1, max_value=12)
    start = serializers.TimeField()
    end = serializers.TimeField()


class BellIn(serializers.Serializer):
    periods = PeriodIn(many=True, max_length=12)

    def validate_periods(self, value):
        numbers = [p["number"] for p in value]
        if len(numbers) != len(set(numbers)):
            raise serializers.ValidationError("شماره زنگ‌ها تکراری است.")
        ordered = sorted(value, key=lambda p: p["start"])
        for p in ordered:
            if p["end"] <= p["start"]:
                raise serializers.ValidationError("پایان هر زنگ باید بعد از شروع آن باشد.")
        for a, b in zip(ordered, ordered[1:]):
            if b["start"] < a["end"]:
                raise serializers.ValidationError("زمان زنگ‌ها هم‌پوشانی دارد.")
        return value


def _school(request, school_id):
    school = get_object_or_404(OrgUnit, pk=school_id, kind=OrgUnit.Kind.SCHOOL)
    if not org_in_scope(request.user, school):
        return None
    return school


class BellView(TimetableView):
    permission_classes = [IsAdmin]

    def get(self, request, school_id):
        school = _school(request, school_id)
        if school is None:
            return Response(status=404)
        return Response([{"number": b.number, "start": hm(b.start_time), "end": hm(b.end_time), "hours": b.hours, "minutes": b.minutes}
                         for b in BellPeriod.objects.filter(school=school)])

    def put(self, request, school_id):
        school = _school(request, school_id)
        if school is None:
            return Response(status=404)
        s = BellIn(data=request.data)
        s.is_valid(raise_exception=True)
        wanted = {p["number"]: p for p in s.validated_data["periods"]}
        with transaction.atomic():
            existing = {b.number: b for b in BellPeriod.objects.select_for_update().filter(school=school)}
            for number, b in existing.items():
                if number not in wanted:
                    if b.entries.exists():
                        return Response({"detail": f"زنگ {number} در برنامه هفتگی استفاده شده و حذف نمی‌شود."}, status=409)
                    b.delete()
            # Temporarily clear changed times so overlapping updates cannot trip the validation order.
            for number, p in wanted.items():
                row = existing.get(number)
                if row is None:
                    BellPeriod.objects.create(school=school, number=number, start_time=p["start"], end_time=p["end"])
                elif (row.start_time, row.end_time) != (p["start"], p["end"]):
                    BellPeriod.objects.filter(pk=row.pk).update(start_time=p["start"], end_time=p["end"])
        audit(request, "timetable.bell", f"school:{school.pk}")
        return Response({"periods": len(wanted)})


class EntryIn(serializers.Serializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    period = serializers.IntegerField(min_value=1, max_value=12)
    course = serializers.IntegerField()
    room = serializers.CharField(required=False, allow_blank=True, max_length=50)


class GridIn(serializers.Serializer):
    entries = EntryIn(many=True, max_length=100)


def _classroom(request, pk):
    """A class inside the admin's own org subtree (super admins: any)."""
    u = request.user
    qs = ClassRoom.objects.select_related("school")
    if u.role != User.Role.SUPER_ADMIN:
        qs = qs.filter(org_scope_q(u, "school"))
    return get_object_or_404(qs, pk=pk)


class GridView(TimetableView):
    """GET/PUT /api/schedule/grid/<classroom_id>/ — the editable grid of one class."""
    permission_classes = [IsAdmin]

    def get(self, request, classroom_id):
        classroom = _classroom(request, classroom_id)
        periods = BellPeriod.objects.filter(school=classroom.school)
        courses = Course.objects.filter(classroom=classroom).select_related("teacher")
        entries = ScheduleEntry.objects.filter(classroom=classroom).select_related("period")
        return Response({
            "classroom": {"id": classroom.pk, "name": classroom.name, "school": classroom.school.name,
                          "school_id": classroom.school_id},
            "weekdays": WEEKDAYS,
            "periods": [{"number": b.number, "start": hm(b.start_time), "end": hm(b.end_time), "hours": b.hours, "minutes": b.minutes} for b in periods],
            "day_hours": sum(b.hours for b in periods),
            "courses": [{"id": c.pk, "title": c.title, "teacher": (c.teacher.get_full_name() or c.teacher.username) if c.teacher_id else ""}
                        for c in courses],
            "entries": [{"weekday": e.weekday, "period": e.period.number, "course": e.course_id, "room": e.room}
                        for e in entries]})

    def put(self, request, classroom_id):
        classroom = _classroom(request, classroom_id)
        s = GridIn(data=request.data)
        s.is_valid(raise_exception=True)
        entries = s.validated_data["entries"]
        periods = {b.number: b for b in BellPeriod.objects.filter(school=classroom.school)}
        courses = {c.pk: c for c in Course.objects.filter(classroom=classroom).select_related("teacher")}
        errors, seen = [], set()
        for e in entries:
            key = (e["weekday"], e["period"])
            if key in seen:
                errors.append(f"روز {WEEKDAYS[e['weekday']]} زنگ {e['period']} دو بار آمده است.")
            seen.add(key)
            if e["period"] not in periods:
                errors.append(f"زنگ {e['period']} برای این مدرسه تعریف نشده است.")
            if e["course"] not in courses:
                errors.append("درس انتخاب‌شده به این کلاس وصل نیست.")
        if errors:
            return Response({"detail": " ".join(dict.fromkeys(errors))}, status=400)
        with transaction.atomic():
            ClassRoom.objects.select_for_update().get(pk=classroom.pk)
            teacher_ids = {courses[e["course"]].teacher_id for e in entries}
            busy = ScheduleEntry.objects.filter(course__teacher_id__in=teacher_ids).exclude(classroom=classroom) \
                .select_related("classroom", "course__teacher")
            busy_map = {(b.course.teacher_id, b.weekday, b.period_id): b for b in busy}
            for e in entries:
                c = courses[e["course"]]
                clash = busy_map.get((c.teacher_id, e["weekday"], periods[e["period"]].pk))
                if clash is not None:
                    errors.append(f"{(c.teacher.get_full_name() or c.teacher.username) if c.teacher_id else 'بدون دبیر'} در {WEEKDAYS[e['weekday']]} زنگ "
                                  f"{e['period']} در کلاس «{clash.classroom.name}» برنامه دارد.")
            if errors:
                return Response({"detail": " ".join(errors)}, status=400)
            ScheduleEntry.objects.filter(classroom=classroom).delete()
            ScheduleEntry.objects.bulk_create([
                ScheduleEntry(classroom=classroom, weekday=e["weekday"], period=periods[e["period"]],
                              course=courses[e["course"]], room=e.get("room", "")) for e in entries])
        audit(request, "timetable.grid", f"classroom:{classroom.pk} n={len(entries)}")
        return Response({"entries": len(entries)})
