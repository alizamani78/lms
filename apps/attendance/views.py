from datetime import date as date_cls, timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.academics.models import ClassRoom
from apps.accounts.models import User
from apps.core.jalali import jalali_str
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_scope_q
from apps.courses.models import Course
from apps.schoolcal.models import blocking_event

from .models import AttendanceChange, AttendanceRecord, slot_key

STAFF = role_required(*User.STAFF_ROLES)
STATUSES = [c[0] for c in AttendanceRecord.Status.choices]


def staff_classrooms(user):
    qs = ClassRoom.objects.all()
    if user.role == User.Role.TEACHER:
        return qs.filter(Q(homeroom_teacher=user) | Q(courses__teacher=user)).distinct()
    return qs.filter(org_scope_q(user, "school"))


def staff_classrooms_in(request):
    from apps.core.scope import kartabl_filter
    return staff_classrooms(request.user).filter(**kartabl_filter(request, "school_id"))


class RecordIn(serializers.Serializer):
    student = serializers.IntegerField()
    status = serializers.ChoiceField(choices=STATUSES)
    minutes_late = serializers.IntegerField(min_value=0, max_value=600, default=0)
    note = serializers.CharField(required=False, allow_blank=True, max_length=200)


class BulkIn(serializers.Serializer):
    classroom = serializers.IntegerField()
    course = serializers.IntegerField(required=False, allow_null=True)
    date = serializers.DateField()
    period = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=12)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)
    records = RecordIn(many=True, min_length=1, max_length=200)


class AttendanceView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"
    permission_classes = [STAFF]

    def get(self, request):
        """GET /api/attendance/?classroom=<id>&date=YYYY-MM-DD[&course=<id>]"""
        p = request.query_params
        if not p.get("classroom", "").isdigit():
            return Response({"detail": "classroom لازم است."}, status=400)
        classroom = get_object_or_404(staff_classrooms(request.user), pk=int(p["classroom"]))
        qs = AttendanceRecord.objects.filter(classroom=classroom).select_related("student")
        if p.get("date"):
            try:
                qs = qs.filter(date=date_cls.fromisoformat(p["date"]))
            except ValueError:
                return Response({"detail": "تاریخ نامعتبر است."}, status=400)
        if p.get("course", "").isdigit():
            qs = qs.filter(course_id=int(p["course"]))
        return Response([{
            "id": r.pk, "student": r.student_id, "username": r.student.username, "name": r.student.get_full_name(),
            "date": r.date, "date_jalali": jalali_str(r.date), "period": r.period, "course": r.course_id, "status": r.status, "minutes_late": r.minutes_late,
            "note": r.note, "source": r.source, "validation": r.validation, "received_at": r.received_at}
            for r in qs.order_by("date", "student__username")[:500]])

    def post(self, request):
        """Bulk record. A first entry needs no reason; changing an existing status requires one and is logged."""
        s = BulkIn(data=request.data)
        s.is_valid(raise_exception=True)
        d, u = s.validated_data, request.user
        classroom = get_object_or_404(staff_classrooms(u), pk=d["classroom"])
        if d["date"] > timezone.localdate():
            raise serializers.ValidationError({"date": "ثبت حضور برای تاریخ آینده مجاز نیست."})
        blocked = blocking_event(classroom.school, d["date"])
        if blocked:
            raise serializers.ValidationError({"date": f"در این تاریخ «{blocked.title}» است و ثبت حضور و غیاب بسته است."})
        course_id = d.get("course")
        if course_id:
            course = get_object_or_404(Course, pk=course_id, classroom=classroom)
            if u.role == User.Role.TEACHER and course.teacher_id != u.pk:
                raise PermissionDenied()
        elif u.role == User.Role.TEACHER and classroom.homeroom_teacher_id != u.pk:
            raise PermissionDenied()           # daily (non-course) attendance: homeroom teacher or admins only
        wanted = {r["student"]: r for r in d["records"]}
        roster = set(roster_qs(classroom, Course.objects.get(pk=course_id) if course_id else None)
                     .filter(pk__in=wanted).values_list("pk", flat=True))
        outsiders = set(wanted) - roster
        if outsiders:
            raise serializers.ValidationError({"records": f"دانش‌آموز(ان) عضو کلاس نیستند: {sorted(outsiders)[:5]}"})
        key, reason = slot_key(course_id, d.get("period")), d.get("reason", "").strip()
        created = changed = 0
        with transaction.atomic():
            existing = {r.student_id: r for r in AttendanceRecord.objects.select_for_update().filter(
                classroom=classroom, date=d["date"], slot_key=key, student_id__in=wanted)}
            for sid, rec in wanted.items():
                row = existing.get(sid)
                if row is None:
                    AttendanceRecord.objects.create(
                        student_id=sid, classroom=classroom, course_id=course_id, date=d["date"],
                        period=d.get("period"), slot_key=key, status=rec["status"], minutes_late=rec["minutes_late"],
                        note=rec.get("note", ""), recorded_by=u)
                    created += 1
                    if rec["status"] == "absent" and d.get("period"):
                        from apps.timetable.models import BellPeriod as _BP
                        bell = _BP.objects.filter(school_id=classroom.school_id, number=d.get("period")).first()
                        if bell is not None:
                            AttendanceRecord.objects.filter(student_id=sid, classroom=classroom, date=d["date"], slot_key=key).update(
                                absence_minutes=bell.minutes, absence_hours=bell.hours)
                    if rec["status"] == "late" and rec["minutes_late"]:
                        _log_lateness(sid, classroom, d, rec["minutes_late"], u)
                elif row.status != rec["status"] or row.minutes_late != rec["minutes_late"]:
                    if not reason:
                        raise serializers.ValidationError({"reason": "برای اصلاح حضور و غیاب ثبت‌شده، دلیل الزامی است."})
                    AttendanceChange.objects.create(record=row, old_status=row.status, new_status=rec["status"],
                                                    reason=reason, changed_by=u)
                    row.status, row.minutes_late, row.note = rec["status"], rec["minutes_late"], rec.get("note", row.note)
                    row.source, row.validation, row.recorded_by = "manual", "manual", u
                    row.save(update_fields=["status", "minutes_late", "note", "source", "validation",
                                            "recorded_by", "updated_at"])
                    changed += 1
        audit(request, "attendance.record", f"classroom:{classroom.pk} {d['date']} +{created} ~{changed}")
        return Response({"created": created, "changed": changed}, status=201 if created else 200)


def summarize(qs):
    counts = {s: 0 for s in STATUSES}
    for row in qs.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    return counts


class MyAttendanceView(APIView):
    """A student's own attendance (last 60 days by default)."""
    permission_classes = [role_required(User.Role.STUDENT)]

    def get(self, request):
        since = timezone.localdate() - timedelta(days=60)
        qs = AttendanceRecord.objects.filter(student=request.user, date__gte=since)
        rows = qs.order_by("-date").values("date", "period", "status", "minutes_late", "note", "course__title")[:300]
        return Response({"summary": summarize(qs), "records": list(rows)})


class ClassroomListView(APIView):
    """Classes the user may record attendance for, with the courses they may use for course/period attendance."""
    permission_classes = [role_required(*User.READ_ROLES)]

    def get(self, request):
        u = request.user
        qs = staff_classrooms_in(request).select_related("school", "grade", "academic_year").prefetch_related("courses")
        out = []
        for c in qs.order_by("school__name", "name")[:200]:
            courses = [x for x in c.courses.all() if u.role != User.Role.TEACHER or x.teacher_id == u.pk]
            out.append({"id": c.pk, "name": c.name, "school": c.school.name, "school_id": c.school_id, "grade": c.grade.name,
                        "year": c.academic_year.name,
                        "daily": u.role != User.Role.TEACHER or c.homeroom_teacher_id == u.pk,
                        "courses": [{"id": x.pk, "title": x.title} for x in courses]})
        return Response(out)


def roster_qs(classroom, course=None):
    """Who is in the attendance list: the students of the class AND the students enrolled in its courses (so a student
    who joined a course later is listed at once). With a course, only that course's enrolled students are added."""
    from apps.courses.models import Enrollment
    member_ids = set(classroom.students.values_list("pk", flat=True))
    enrolled = Enrollment.objects.filter(course=course) if course is not None else Enrollment.objects.filter(course__classroom=classroom)
    member_ids |= set(enrolled.values_list("student_id", flat=True))
    return User.objects.filter(pk__in=member_ids, role="student")


class RosterView(APIView):
    permission_classes = [STAFF]

    def get(self, request, pk):
        classroom = get_object_or_404(staff_classrooms(request.user), pk=pk)
        return Response([{"id": s.pk, "username": s.username, "name": s.get_full_name()}
                         for s in roster_qs(classroom).order_by("last_name", "username")[:500]])


def _log_lateness(student_id, classroom, d, minutes, user):
    """تأخیر هر دانش‌آموز یک سابقه در کارنامه‌ی انضباطی می‌شود (با مدت تأخیر و زنگ)."""
    from apps.discipline.models import DisciplineFactor, DisciplineRecord
    from apps.accounts.models import User
    student = User.objects.filter(pk=student_id).first()
    if student is None or not student.org_id:
        return
    factor, _ = DisciplineFactor.objects.get_or_create(
        school_id=student.org_id, title="تأخیر به کلاس", defaults={"points": 0, "is_active": True})
    period = f"زنگ {d.get('period')}" if d.get("period") else "بدون زنگ"
    DisciplineRecord.objects.create(
        student=student, school_id=student.org_id, factor=factor, title=factor.title, points=0,
        date=d["date"], note=f"تأخیر {minutes} دقیقه — {period}", recorded_by=user)
