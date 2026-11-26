"""Adding the lessons of a class: the school → the class → the lessons of that class's grade (general, required and
elective of the grade) → one or more lessons for one teacher, in one step. Duplicates are never added."""
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, Subject
from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

ADDERS = ("super_admin", "school_admin", "school_deputy_edu", "school_deputy_exec")


def _check(user, school):
    if user.role not in ADDERS or not org_in_scope(user, school):
        raise PermissionDenied("افزودن درس‌های کلاس را مدیر یا معاونان مدرسه انجام می‌دهند.")


class ClassesView(APIView):
    """GET ?school=ID — the classes of the school, in the current academic year."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        rows = ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field").order_by("grade__order", "name")
        return Response([{"id": c.pk, "label": f"{c.grade.name} — {c.name}" + (f" ({c.study_field.name})" if c.study_field else "")} for c in rows])


def _subjects_for(cls):
    qs = Subject.objects.filter(grades=cls.grade)
    if cls.study_field_id:
        qs = qs.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True) | Q(study_fields=cls.study_field_id))
    else:
        qs = qs.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True))
    return qs.distinct()


class ClassSubjectsView(APIView):
    """GET ?classroom=ID — the lessons of the class's grade: general, required and elective; the lessons of its field."""

    def get(self, request):
        cls = get_object_or_404(ClassRoom.objects.select_related("school", "grade", "study_field"), pk=request.query_params.get("classroom") or 0)
        _check(request.user, cls.school)
        qs = Subject.objects.filter(grades=cls.grade)
        if cls.study_field_id:
            qs = qs.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True) | Q(study_fields=cls.study_field_id))
        else:
            qs = qs.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True))
        qs = qs.distinct().order_by("name")
        taken = set(Course.objects.filter(classroom=cls, subject__isnull=False).values_list("subject_id", flat=True))
        return Response([{"id": s.pk, "code": s.code, "name": s.name, "type": s.subject_type or "",
                          "sharing": s.get_sharing_display() if s.sharing else "عمومی", "taken": s.pk in taken} for s in qs])


class TeachersView(APIView):
    """GET ?school=ID — the teachers of the school."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        rows = User.objects.filter(role="teacher", is_active=True).filter(Q(org=school) | Q(school_memberships__school=school)).distinct().order_by("last_name", "username")
        return Response([{"id": t.pk, "label": t.get_full_name() or t.username} for t in rows])


class CourseBulkView(APIView):
    """POST {classroom, teacher, subjects:[ids]} — adds the chosen lessons of the class for that teacher.
    Lessons already in the class are reported as duplicates and are not added."""

    def post(self, request):
        cls = get_object_or_404(ClassRoom.objects.select_related("school", "academic_year", "grade", "study_field"), pk=request.data.get("classroom") or 0)
        _check(request.user, cls.school)
        teacher = get_object_or_404(User, pk=request.data.get("teacher") or 0, role="teacher")
        ids = [int(x) for x in (request.data.get("subjects") or []) if str(x).isdigit()]
        if not ids:
            raise serializers.ValidationError({"subjects": "حداقل یک درس را انتخاب کنید."})
        allowed = {s.pk: s for s in _subjects_for(cls)}
        added, duplicates, refused = [], [], []
        for sid in ids:
            s = allowed.get(sid)
            if s is None:
                refused.append(str(sid)); continue
            if Course.objects.filter(classroom=cls, subject=s).exists():
                duplicates.append(s.name); continue
            Course.objects.create(org=cls.school, title=s.name, teacher=teacher, subject=s, classroom=cls, academic_year=cls.academic_year)
            added.append(s.name)
        audit(request, "course.bulk", f"class:{cls.pk} added={len(added)} duplicates={len(duplicates)}")
        msg = f"کلاس «{cls.grade.name} — {cls.name}»: {len(added)} درس اضافه شد"
        if duplicates:
            msg += f"؛ {len(duplicates)} درس از قبل در این کلاس بود و اضافه نشد"
        return Response({"added": added, "duplicates": duplicates, "refused": refused, "message": msg}, status=201)


class ArrangeLessonsView(APIView):
    """POST {school} — برای هر کلاس سال جاری، درس‌های پایه و رشته‌ی آن کلاس (عمومی، اجباری و اختیاری) به‌صورت خودکار
    چیده می‌شود؛ معلم بعداً تعیین می‌شود. درس تکراری در یک کلاس ساخته نمی‌شود."""

    def post(self, request):
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        _check(request.user, school)
        from apps.academics.models import ClassRoom as CR
        made = 0
        classes = CR.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field", "academic_year")
        for cls in classes:
            qs = Subject.objects.filter(grades=cls.grade)
            if cls.study_field_id:
                qs = qs.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True) | Q(study_fields=cls.study_field_id))
            else:
                qs = qs.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True))
            taken = set(Course.objects.filter(classroom=cls, subject__isnull=False).values_list("subject_id", flat=True))
            for sub in qs.distinct().order_by("name"):
                if sub.pk in taken:
                    continue
                Course.objects.create(org=school, classroom=cls, subject=sub, title=sub.name[:200],
                                      academic_year=cls.academic_year, teacher=None)
                made += 1
        audit(request, "course.arrange", f"school:{school.pk} made:{made}")
        return Response({"ok": True, "made": made, "classes": classes.count()})


class TeacherSubjectsView(APIView):
    """GET ?school=ID — جفت‌های دبیر و درس مدرسه (از درس‌های کلاس‌ها)، برای انتخاب دبیر و درس به هم وابسته."""

    def get(self, request):
        school = get_object_or_404(OrgUnit, pk=request.query_params.get("school") or 0, kind="school")
        _check(request.user, school)
        pairs = Course.objects.filter(org=school, teacher__isnull=False, subject__isnull=False) \
            .values("teacher_id", "teacher__first_name", "teacher__last_name", "teacher__username", "subject_id", "subject__name") \
            .distinct().order_by("subject__name")
        return Response([{"teacher": p["teacher_id"],
                          "teacher_name": (f"{p['teacher__first_name']} {p['teacher__last_name']}").strip() or p["teacher__username"],
                          "subject": p["subject_id"], "subject_name": p["subject__name"]} for p in pairs])


class ClassSubjectsReasonView(APIView):
    """GET ?classroom=ID — چرا برای این کلاس درسی پیدا نشد (با علت دقیق و راه‌حل)."""

    def get(self, request):
        cls = get_object_or_404(ClassRoom.objects.select_related("school", "grade", "study_field"), pk=request.query_params.get("classroom") or 0)
        _check(request.user, cls.school)
        grade_name = cls.grade.name if cls.grade_id else "—"
        field_name = cls.study_field.name if cls.study_field_id else ""
        for_grade = Subject.objects.filter(grades=cls.grade).distinct()
        if not cls.grade_id or not for_grade.exists():
            return Response({"reason": f"هیچ درسی به پایه‌ی «{grade_name}» وصل نیست. در بخش «درس‌ها و پایه»، درس‌های این پایه را وصل کنید."})
        if not cls.study_field_id:
            if not for_grade.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True)).exists():
                return Response({"reason": f"کلاس «{cls.name}» رشته ندارد و همه‌ی دروس پایه‌ی «{grade_name}» مخصوص رشته هستند. رشته‌ی کلاس را در «ساختار کلاس‌ها» تعیین کنید."})
        else:
            fields = sorted({f.name for s0 in for_grade for f in s0.study_fields.all()})
            if not for_grade.filter(Q(sharing="general") | Q(sharing="", study_fields__isnull=True) | Q(study_fields=cls.study_field_id)).exists():
                listed = "، ".join(fields) if fields else "—"
                return Response({"reason": f"دروس پایه‌ی «{grade_name}» برای رشته‌ی «{field_name}» تعریف نشده‌اند. این دروس فقط برای رشته‌های: {listed} هستند. اگر این کلاس باید همین دروس را داشته باشد، رشته‌ی درس‌ها یا رشته‌ی کلاس را بررسی کنید."})
        return Response({"reason": "درس‌های مناسب این کلاس همه قبلاً ثبت شده‌اند یا فهرست خالی است."})

