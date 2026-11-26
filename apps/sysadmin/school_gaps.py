"""کمبودهای مدرسه در سال جاری: دانش‌آموز بدون کلاس، معلم بدون درس، کلاس و پایه بدون درس؛
و تکمیل آن‌ها: افزودن دانش‌آموز به کلاس، معلم به درس، درس به کلاس."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, Subject
from apps.accounts.models import OrgUnit, SchoolMembership, User
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

ROLES = ("super_admin", "school_admin", "school_deputy_edu")


def _school(request, param):
    school = get_object_or_404(OrgUnit, pk=param or 0, kind="school")
    u = request.user
    if u.role not in ROLES or not (u.role == "super_admin" or org_in_scope(u, school)):
        raise PermissionDenied("این بخش را مدیر یا معاون آموزشی مدرسه می‌بیند.")
    return school


def _name(u):
    return u.get_full_name() or u.username


class SchoolGapsView(APIView):
    """GET ?school=ID — کمبودها و فهرست‌های لازم برای تکمیل."""

    def get(self, request):
        school = _school(request, request.query_params.get("school"))
        classes = list(ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field"))
        class_ids = [c.pk for c in classes]
        in_class = set(User.objects.filter(role="student", classrooms__in=class_ids).values_list("pk", flat=True))
        students = User.objects.filter(role="student", org=school).exclude(pk__in=in_class).order_by("last_name", "first_name")
        teacher_ids = SchoolMembership.objects.filter(school=school, user__role="teacher").values_list("user_id", flat=True)
        teachers_all = User.objects.filter(pk__in=teacher_ids).order_by("last_name", "first_name")
        taught = set(Course.objects.filter(org=school, teacher__isnull=False).values_list("teacher_id", flat=True))
        teachers_free = [t for t in teachers_all if t.pk not in taught]
        courses_per_class = {}
        for cid, in Course.objects.filter(org=school, classroom_id__in=class_ids).values_list("classroom_id").distinct():
            courses_per_class[cid] = True
        empty_classes = [c for c in classes if c.pk not in courses_per_class]
        unassigned_courses = Course.objects.filter(org=school, teacher__isnull=True).select_related("classroom", "subject")
        return Response({
            "students_no_class": [{"id": s.pk, "name": _name(s), "code": s.student_code or "", "national": s.national_id or ""} for s in students[:500]],
            "teachers_no_course": [{"id": t.pk, "name": _name(t), "national": t.national_id or ""} for t in teachers_free],
            "classes_no_course": [{"id": c.pk, "label": f"{c.grade.name} — {c.name}", "grade": c.grade.name,
                                   "field": c.study_field.name if c.study_field_id else ""} for c in empty_classes],
            "courses_no_teacher": [{"id": c.pk, "title": c.title, "class": c.classroom.name if c.classroom_id else ""} for c in unassigned_courses[:500]],
            "choices": {
                "classes": [{"id": c.pk, "label": f"{c.grade.name} — {c.name}"} for c in classes],
                "teachers": [{"id": t.pk, "label": f"{_name(t)} — {t.national_id or ''}"} for t in teachers_all],
                "subjects": [{"id": s.pk, "label": s.name} for s in Subject.objects.filter(pk__in=Course.objects.filter(org=school).values("subject_id")).order_by("name")],
            },
        })


class SchoolGapsActionView(APIView):
    """POST {school, action, …}:
      assign_student {student, classroom}   — افزودن دانش‌آموز به کلاس
      assign_teacher {course, teacher}      — تعیین معلم برای درس کلاس
      add_course {classroom, subject, teacher?} — افزودن درس به کلاس (با معلم اختیاری)"""

    @transaction.atomic
    def post(self, request):
        school = _school(request, request.data.get("school"))
        action = request.data.get("action")
        if action == "assign_student":
            s = get_object_or_404(User, pk=request.data.get("student") or 0, role="student")
            c = get_object_or_404(ClassRoom, pk=request.data.get("classroom") or 0, school=school, academic_year__is_current=True)
            from apps.academics.assign import other_class_of
            other = other_class_of(s, c)
            if other is not None:
                raise serializers.ValidationError({"student": f"{_name(s)} قبلاً در کلاس «{other.name}» ثبت شده است."})
            c.students.add(s)
            audit(request, "gaps.assign_student", f"student:{s.pk} class:{c.pk}")
            return Response({"ok": True})
        if action == "assign_teacher":
            course = get_object_or_404(Course, pk=request.data.get("course") or 0, org=school)
            t = get_object_or_404(User, pk=request.data.get("teacher") or 0, role="teacher")
            course.teacher = t
            course.save(update_fields=["teacher"])
            audit(request, "gaps.assign_teacher", f"course:{course.pk} teacher:{t.pk}")
            return Response({"ok": True})
        if action == "add_course":
            c = get_object_or_404(ClassRoom, pk=request.data.get("classroom") or 0, school=school, academic_year__is_current=True)
            sub = get_object_or_404(Subject, pk=request.data.get("subject") or 0)
            t = None
            if request.data.get("teacher"):
                t = get_object_or_404(User, pk=request.data.get("teacher"), role="teacher")
            if Course.objects.filter(org=school, classroom=c, subject=sub).exists():
                raise serializers.ValidationError({"subject": "این درس قبلاً برای این کلاس ثبت شده است."})
            course = Course.objects.create(org=school, classroom=c, subject=sub, title=sub.name[:200],
                                           academic_year=c.academic_year, teacher=t)
            audit(request, "gaps.add_course", f"course:{course.pk}")
            return Response({"ok": True, "course": course.pk})
        raise serializers.ValidationError({"action": "عملیات نامعتبر است."})
