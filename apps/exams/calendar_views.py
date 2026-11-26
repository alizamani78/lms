"""تقویم امتحانات (مثل برنامه‌ی رسمی): روز و تاریخ در ردیف‌ها، کلاس‌ها در ستون‌ها، درس در خانه.

- مدیر مدرسه، معاون آموزشی، مدیر سیستم: همه‌ی کلاس‌ها؛ ویرایش و حذف داخل همین جدول.
- معلم: کلاس‌هایی که درس می‌دهد. دانش‌آموز: کلاس خودش. ولی: کلاس فرزندان تأییدشده‌ی خودش. (فقط خواندن)
"""
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, ParentLink, SchoolMembership
from apps.academics.models import ClassRoom
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

from .models import ExamSchedule, ExamScheduleEntry
from .schedule_export import color_for
from .schedule_views import fa_day

MANAGERS = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec")


def _school_for(user, param):
    if param:
        return get_object_or_404(OrgUnit, pk=param, kind="school")
    m = SchoolMembership.objects.filter(user=user).select_related("school").first()
    if m and m.school:
        return m.school
    # a student, a parent or a teacher is attached to the school through the user's own school
    org = getattr(user, "org", None)
    return org if org is not None and org.kind == "school" else None


def _visible_classes(user, school):
    """کلاس‌هایی که این کاربر در تقویم می‌بیند (None یعنی همه)."""
    if user.role == "super_admin" or (user.role in MANAGERS and org_in_scope(user, school)):
        return None
    if user.role == "teacher":
        return set(Course.objects.filter(org=school, teacher=user, classroom__isnull=False).values_list("classroom_id", flat=True))
    if user.role == "student":
        return set(ClassRoom.objects.filter(school=school, students=user).values_list("pk", flat=True))
    if user.role == "parent":
        kids = ParentLink.objects.filter(parent=user, is_verified=True).values_list("student_id", flat=True)
        return set(ClassRoom.objects.filter(school=school, students__in=kids).values_list("pk", flat=True))
    return set()


class ExamCalendarView(APIView):
    """GET ?school=ID&schedule=ID — تقویم امتحانات (آخرین برنامه‌ی تأییدشده، یا برنامه‌ی مشخص‌شده)."""

    def get(self, request):
        school = _school_for(request.user, request.query_params.get("school"))
        if school is None:
            return Response({"schedule": None, "entries": [], "editable": False})
        qs = ExamSchedule.objects.filter(school=school)
        if request.query_params.get("schedule"):
            sch = qs.filter(pk=request.query_params["schedule"]).first()
        else:
            sch = qs.filter(status=ExamSchedule.APPROVED).order_by("-approved_at", "-id").first()
        if sch is None:
            return Response({"schedule": None, "entries": [], "editable": False})
        allowed = _visible_classes(request.user, school)
        entries = ExamScheduleEntry.objects.filter(schedule=sch).select_related("classroom__grade", "subject").order_by("date", "start_time")
        if allowed is not None:
            entries = entries.filter(classroom_id__in=allowed)
        # editing is only for the managers, and only over the whole school
        editable = request.user.role in MANAGERS and allowed is None
        return Response({
            "schedule": {"id": sch.pk, "title": sch.title, "status": sch.status, "status_label": sch.get_status_display()},
            "editable": editable,
            "entries": [{"id": e.pk, "classroom": e.classroom_id, "classroom_name": f"{e.classroom.grade.name} — {e.classroom.name}",
                         "subject": e.subject_id, "subject_name": e.subject.name, "date": e.date.isoformat(),
                         "date_fa": fa_day(e.date), "start_time": e.start_time.strftime("%H:%M"), "duration": e.duration_minutes,
                         "venue_name": e.venue_name, "venue_address": e.venue_address,
                         "color": color_for(e.classroom.grade.name if e.classroom.grade_id else "", e.classroom.name)} for e in entries],
        })


def entry_exam_for_lock(entry):
    """the exam of a scheduled entry, for the lock check (the score is kept in the course's final grade)"""
    from .models import Exam
    return Exam.objects.filter(course__classroom=entry.classroom, course__subject=entry.subject).first()


class ScheduleEntryScoresView(APIView):
    """GET ?entry=ID — دانش‌آموزان کلاس این امتحان با نمره‌ی ثبت‌شده (اگر باشد).
    POST {entry, scores:{student_id: نمره}} — ثبت نمره‌ی امتحان بدون آزمون آنلاین (در نمره‌ی پایانی درس)."""

    def _entry(self, request, pk):
        from django.shortcuts import get_object_or_404
        from apps.courses.models import Course as C
        from apps.evaluation.models import ClassGrade
        from .models import ExamScheduleEntry as E
        entry = get_object_or_404(E.objects.select_related("schedule__school", "classroom__grade", "subject"), pk=pk)
        school = entry.schedule.school
        is_manager = request.user.role in MANAGERS and org_in_scope(request.user, school)
        course = C.objects.filter(classroom=entry.classroom, subject=entry.subject).first()
        is_teacher = course is not None and course.teacher_id == request.user.pk
        if not (is_manager or is_teacher):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("نمره‌ی این امتحان را دبیر همان درس یا مدیر مدرسه ثبت می‌کند.")
        title = f"امتحان {entry.subject.name} ({entry.date.isoformat()})"
        # the teacher's students for this subject in this grade (all the grade's classes that the teacher teaches)
        self._is_teacher = is_teacher
        return entry, course, title, ClassGrade

    def _exam_obj(self, entry, course, title):
        from .models import Exam as _Ex
        return _Ex.objects.filter(course=course, title=title).first() if course else None

    def _students(self, entry):
        from apps.accounts.models import User
        from apps.academics.models import ClassRoom
        from apps.courses.models import Course
        # the grade's current classes where this subject is taught; the teacher sees only the classes he teaches
        # the list is only the students of this exam's class
        return User.objects.filter(role="student", classrooms=entry.classroom).distinct().order_by("last_name", "first_name", "id")

    def get(self, request):
        self.request = request
        from apps.accounts.models import User
        entry, course, title, ClassGrade = self._entry(request, request.query_params.get("entry") or 0)
        students = self._students(entry)
        current = {g.student_id: float(g.score) for g in ClassGrade.objects.filter(course=course, title=title)} if course else {}
        from .models import Exam as _Ex
        from .score_review import ExamScoreStatus
        exam_obj = _Ex.objects.filter(course=course, title=title).first() if course else None
        notes = {x.student_id: x.teacher_note for x in ExamScoreStatus.objects.filter(exam=exam_obj)} if exam_obj else {}
        return Response({"title": title, "max": 20, "editable": bool(getattr(self, "_is_teacher", False)), "class": entry.classroom.name,
                         "exam": exam_obj.pk if exam_obj else None, "monthly_percent": exam_obj.monthly_percent if exam_obj else None, "students": [
            {"id": s.pk, "name": s.get_full_name() or s.username, "code": s.student_code or "",
             "national": s.national_id or "", "class": entry.classroom.name, "score": current.get(s.pk),
             "note": notes.get(s.pk, "")} for s in students]})

    def post(self, request):
        from decimal import Decimal, InvalidOperation
        self.request = request
        entry, course, title, ClassGrade = self._entry(request, request.data.get("entry") or 0)
        if not getattr(self, "_is_teacher", False):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("ثبت نمره را دبیر همان درس انجام می‌دهد؛ مدیر فقط می‌بیند.")
        # the teacher's notes for the students of this exam (saved with the scores)
        exam_obj = self._exam_obj(entry, course, title)
        if exam_obj is not None:
            from .score_review import ExamScoreStatus
            for sid, note in (request.data.get("notes") or {}).items():
                if str(sid).isdigit():
                    st, _ = ExamScoreStatus.objects.get_or_create(exam=exam_obj, student_id=int(sid))
                    st.teacher_note = str(note or "")[:1000]
                    st.save(update_fields=["teacher_note"])
        if course is None:
            return Response({"ok": False, "errors": ["برای این کلاس و درس، درسی در کلاس‌ها ثبت نشده است."]}, status=422)
        allowed = set(self._students(entry).values_list("pk", flat=True))
        saved = 0
        from .score_review import is_locked
        for sid, raw in (request.data.get("scores") or {}).items():
            if raw in (None, "") or int(sid) not in allowed or is_locked(entry_exam_for_lock(entry), int(sid)):
                continue
            try:
                score = Decimal(str(raw))
            except InvalidOperation:
                return Response({"ok": False, "errors": ["نمره‌ها باید عدد باشند."]}, status=422)
            if score < 0 or score > 20:
                return Response({"ok": False, "errors": ["نمره‌ها بین ۰ تا ۲۰ باشند."]}, status=422)
            ClassGrade.objects.update_or_create(course=course, student_id=int(sid), title=title, period="final",
                                                defaults={"score": score, "max_score": 20, "date": entry.date, "recorded_by": request.user})
            saved += 1
        audit(request, "exam.schedule.scores", f"entry:{entry.pk} saved:{saved}")
        return Response({"ok": True, "saved": saved})


class ExamManualScoresView(APIView):
    """GET /api/exams/<pk>/manual-scores/ — دانش‌آموزان درس، نمره‌ی دستی، نمره‌ی آزمون مجازی و نمره‌ی نهایی طبق ترکیب.
    POST {mode, scores:{student_id: نمره}} — ثبت نمره‌ی دستی و ترکیب؛ نمره‌ی نهایی در نمره‌ی پایانی درس نوشته می‌شود."""

    def _exam(self, request, pk):
        from django.shortcuts import get_object_or_404
        from rest_framework.exceptions import PermissionDenied
        from .models import Exam
        exam = get_object_or_404(Exam.objects.select_related("course__org", "course__teacher"), pk=pk)
        course = exam.course
        is_manager = request.user.role in MANAGERS and org_in_scope(request.user, course.org)
        if not (is_manager or course.teacher_id == request.user.pk):
            raise PermissionDenied("نمره‌ی این آزمون را دبیر همان درس یا مدیر مدرسه ثبت می‌کند.")
        return exam, course

    def get(self, request, pk):
        from apps.accounts.models import User
        from apps.courses.roster import roster_ids
        from .models import ExamManualScore
        from .score_combine import combine, online20
        exam, course = self._exam(request, pk)
        students = User.objects.filter(pk__in=roster_ids(course), role="student").order_by("last_name", "first_name", "id")
        manual = {m.student_id: float(m.score) for m in ExamManualScore.objects.filter(exam=exam)}
        rows = []
        for s in students:
            on = online20(exam, s.pk)
            fin = combine(exam.combine_mode, manual.get(s.pk), on)
            rows.append({"id": s.pk, "name": s.get_full_name() or s.username, "code": s.student_code or "",
                         "national": s.national_id or "", "manual": manual.get(s.pk), "online": float(on) if on is not None else None,
                         "final": float(fin) if fin is not None else None})
        return Response({"exam": exam.title, "mode": exam.combine_mode, "max": 20, "students": rows})

    def post(self, request, pk):
        from decimal import InvalidOperation
        from .models import ExamManualScore
        from .score_combine import apply_exam
        exam, course = self._exam(request, pk)
        if request.data.get("monthly_percent") not in (None, ""):
            try:
                pct = int(request.data.get("monthly_percent"))
            except (TypeError, ValueError):
                return Response({"ok": False, "errors": ["درصد باید عدد باشد."]}, status=422)
            if not 0 <= pct <= 100:
                return Response({"ok": False, "errors": ["درصد نمره‌ی ماهیانه بین ۰ تا ۱۰۰ باشد."]}, status=422)
            exam.monthly_percent = pct
            exam.save(update_fields=["monthly_percent"])
        elif "monthly_percent" in request.data:
            exam.monthly_percent = None
            exam.save(update_fields=["monthly_percent"])
        mode = request.data.get("mode")
        if mode:
            if mode not in dict(exam.COMBINE_CHOICES):
                return Response({"ok": False, "errors": ["ترکیب نمره‌ها درست نیست."]}, status=422)
            exam.combine_mode = mode
            exam.save(update_fields=["combine_mode"])
        saved = 0
        from .score_review import is_locked
        for sid, raw in (request.data.get("scores") or {}).items():
            if is_locked(exam, int(sid)):
                continue
            if raw in (None, ""):
                ExamManualScore.objects.filter(exam=exam, student_id=int(sid)).delete()
                continue
            try:
                score = Decimal(str(raw))
            except InvalidOperation:
                return Response({"ok": False, "errors": ["نمره‌ها باید عدد باشند."]}, status=422)
            if score < 0 or score > 20:
                return Response({"ok": False, "errors": ["نمره‌ها بین ۰ تا ۲۰ باشند."]}, status=422)
            ExamManualScore.objects.update_or_create(exam=exam, student_id=int(sid), defaults={"score": score})
            saved += 1
        from apps.courses.roster import roster_ids
        apply_exam(exam, roster_ids(course))
        audit(request, "exam.manual_scores", f"exam:{exam.pk} saved:{saved} mode:{exam.combine_mode}")
        return Response({"ok": True, "saved": saved, "mode": exam.combine_mode})
