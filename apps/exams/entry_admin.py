"""کارت ورود به جلسه، در پنل مدیر یا معاون آموزشی (جدا از فرم آزمون):
انتخاب مدرسه، آزمون، و داوطلبان به‌صورت کلاس / پایه / یک یا چند دانش‌آموز؛ ساخت شماره‌ی داوطلبی و چاپ."""
from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, Grade
from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.scope import org_in_scope

from .models import Exam, ExamSeat

ROLES = ("super_admin", "school_admin", "school_deputy_edu")


def _check(user, school):
    if user.role not in ROLES or not org_in_scope(user, school):
        raise PermissionDenied("کارت ورود را مدیر یا معاون آموزشی مدرسه صادر می‌کند.")


class EntryOptionsView(APIView):
    """GET ?school=ID — آزمون‌های مدرسه، کلاس‌ها و پایه‌های سال جاری."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        from django.db.models import Q
        exams = (Exam.objects.filter(Q(course__org=school) | Q(course__classroom__school=school)).distinct()
                 .select_related("course").order_by("-starts_at")[:100])
        classes = ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade").order_by("grade__order", "name")
        from apps.accounts.models import SchoolProfile
        prof = SchoolProfile.objects.filter(school=school).select_related("level").first()
        if prof and prof.level_id:
            grades = Grade.objects.filter(level_id=prof.level_id).select_related("branch", "level").order_by("name")
        else:
            grades = Grade.objects.filter(classrooms__school=school, classrooms__academic_year__is_current=True).distinct().order_by("name")
        from .models import ExamSchedule, ExamScheduleEntry
        # the school's exam schedules; each exam knows the schedule it belongs to (an exam made from the schedule)
        scheds = ExamSchedule.objects.filter(school=school).exclude(status="archived").order_by("-created_at")[:50]
        exam_sched = dict(ExamScheduleEntry.objects.filter(exam__in=exams).values_list("exam_id", "schedule_id"))
        return Response({
            "schedules": [{"id": x.pk, "label": f"{x.title} ({x.get_status_display()})"} for x in scheds],
            "exams": [{"id": e.pk, "label": f"{e.title} — {e.course.title}", "schedule": exam_sched.get(e.pk)} for e in exams],
            "classes": [{"id": c.pk, "label": f"{c.grade.name} — {c.name}", "grade": c.grade_id} for c in classes],
            "grades": [{"id": g.pk, "label": g.name} for g in grades],
        })


class EntryStudentsView(APIView):
    """GET ?school=ID&classroom=ID | &grade=ID — دانش‌آموزان کلاس یا پایه (با شماره‌ی داوطلبی اگر ساخته شده)."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        qs = ClassRoom.objects.filter(school=school, academic_year__is_current=True)
        if request.query_params.get("classroom"):
            qs = qs.filter(pk=request.query_params["classroom"])
        elif request.query_params.get("grade"):
            qs = qs.filter(grade_id=request.query_params["grade"])
        else:
            qs = qs.none()
        students = User.objects.filter(classrooms__in=qs).distinct().order_by("last_name", "first_name", "id")
        seats = {}
        if request.query_params.get("exam"):
            seats = {s.student_id: s.number for s in ExamSeat.objects.filter(exam_id=request.query_params["exam"])}
        # the class of each student in this school (for the list and the card)
        cls_of = {}
        for c in qs.select_related("grade").order_by("name"):
            for sid in c.students.values_list("pk", flat=True):
                cls_of.setdefault(sid, f"{c.grade.name} — {c.name}")
        return Response([{"id": s.pk, "name": s.get_full_name() or s.username, "first_name": s.first_name or "",
                          "last_name": s.last_name or "", "code": s.student_code or "", "national": s.national_id or "",
                          "class": cls_of.get(s.pk, ""), "number": seats.get(s.pk)} for s in students])


class EntrySeatsView(APIView):
    """POST {exam, students:[ids], room?, start?} — شماره‌ی داوطلبی برای دانش‌آموزان انتخاب‌شده (فقط کسانی که ندارند)."""

    def post(self, request):
        exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=request.data.get("exam") or 0)
        _check(request.user, exam.course.org)
        ids = [int(x) for x in (request.data.get("students") or []) if str(x).isdigit()]
        if not ids:
            raise serializers.ValidationError({"students": "حداقل یک دانش‌آموز را انتخاب کنید."})
        taken = set(ExamSeat.objects.filter(exam=exam).values_list("student_id", flat=True))
        typed = int(request.data.get("start") or 0)
        # شماره‌ی شروع همان عددی است که وارد شده؛ اگر خالی بود، ادامه‌ی شماره‌ها
        nxt = typed if typed > 0 else max((ExamSeat.objects.filter(exam=exam).aggregate(m=Max("number"))["m"] or 0) + 1, 1)
        room = str(request.data.get("room") or "")[:60]
        made = 0
        for sid in ids:
            if sid in taken:
                continue
            while ExamSeat.objects.filter(exam=exam, number=nxt).exists():
                nxt += 1
            ExamSeat.objects.create(exam=exam, student_id=sid, number=nxt, room=room)
            nxt += 1
            made += 1
        audit(request, "exam.seats.assign", f"exam:{exam.pk} created:{made}")
        return Response({"created": made, "already": len(ids) - made}, status=201)


class EntryRenumberView(APIView):
    """POST {exam, exclude:[ids], start?} — دانش‌آموزان حذف‌شده از این آزمون کنار می‌روند و شماره‌ی بقیه
    از نو و پشت‌سرهم (از همان شماره‌ی شروع) داده می‌شود؛ شماره‌های قبلی آن‌ها خالی می‌شود."""

    def post(self, request):
        exam = get_object_or_404(Exam.objects.select_related("course__org"), pk=request.data.get("exam") or 0)
        _check(request.user, exam.course.org)
        drop = [int(x) for x in (request.data.get("exclude") or []) if str(x).isdigit()]
        removed = ExamSeat.objects.filter(exam=exam, student_id__in=drop).delete()[0] if drop else 0
        seats = list(ExamSeat.objects.filter(exam=exam).order_by("number", "id"))
        start = int(request.data.get("start") or 0) or (seats[0].number if seats else 1)
        # دو مرحله: ابتدا شماره‌های موقت (برای جلوگیری از تداخل یکتایی) و بعد شماره‌های نهایی
        for seat in seats:
            ExamSeat.objects.filter(pk=seat.pk).update(number=-seat.pk)
        for i, seat in enumerate(seats):
            ExamSeat.objects.filter(pk=seat.pk).update(number=start + i)
        audit(request, "exam.seats.renumber", f"exam:{exam.pk} removed:{removed}")
        return Response({"removed": removed, "renumbered": len(seats), "start": start}, status=200)
