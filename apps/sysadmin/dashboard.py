"""داشبورد مدیر مدرسه: نام مدرسه، دانش‌آموز به تفکیک رشته/پایه/کلاس، تعداد کلاس، ساعت تدریس هر رشته، معلم و کادر."""
from collections import defaultdict

from django.db.models import Count, Q
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassRoom, GradeWeeklyHours
from apps.accounts.models import OrgUnit, SchoolMembership, User
from apps.core.scope import org_in_scope
from apps.courses.models import Course


class SchoolDashboardView(APIView):
    """GET ?school=ID — آمار مدرسه برای داشبورد."""

    def get(self, request):
        u = request.user
        sid = request.query_params.get("school")
        if not str(sid or "").isdigit():
            raise serializers.ValidationError({"school": "ابتدا مدرسه را در کارتابل انتخاب کنید."})
        school = OrgUnit.objects.filter(pk=int(sid), kind="school").first()
        if school is None:
            raise serializers.ValidationError({"school": "مدرسه پیدا نشد."})
        if not (u.role == "super_admin" or (u.role in ("school_admin", "school_deputy_edu", "school_deputy_exec") and org_in_scope(u, school))):
            raise PermissionDenied("داشبورد را مدیر یا معاون همان مدرسه می‌بیند.")

        classes = list(ClassRoom.objects.filter(school=school, academic_year__is_current=True).select_related("grade", "study_field"))
        cls_ids = [c.pk for c in classes]
        students_by_class = defaultdict(int)
        for row in User.objects.filter(role="student", classrooms__in=cls_ids).values("classrooms").annotate(n=Count("id", distinct=True)):
            students_by_class[row["classrooms"]] = row["n"]

        by_field = defaultdict(lambda: {"students": 0, "classes": 0, "hours": 0})
        by_grade = defaultdict(lambda: {"students": 0, "classes": 0, "hours": 0})
        class_rows = []
        for c in classes:
            field = c.study_field.name if c.study_field_id else "بدون رشته"
            grade = c.grade.name if c.grade_id else "—"
            n = students_by_class.get(c.pk, 0)
            by_field[field]["students"] += n
            by_field[field]["classes"] += 1
            by_grade[grade]["students"] += n
            by_grade[grade]["classes"] += 1
            class_rows.append({"id": c.pk, "name": c.name, "grade": grade, "field": field, "students": n, "hours": 0})

        # teaching hours per field: each course's weekly units (from the subject settings)
        for co in Course.objects.filter(classroom_id__in=cls_ids, subject__isnull=False).select_related("classroom__grade", "classroom__study_field", "subject"):
            c = co.classroom
            gw = GradeWeeklyHours.objects.filter(grade=c.grade, subject=co.subject, field=c.study_field).first() \
                or GradeWeeklyHours.objects.filter(grade=c.grade, subject=co.subject, field__isnull=True).first()
            hours = gw.hours if gw else (co.subject.weekly_hours or 0)
            field = c.study_field.name if c.study_field_id else "بدون رشته"
            by_field[field]["hours"] += hours
            by_grade[c.grade.name if c.grade_id else "—"]["hours"] += hours
            for row in class_rows:
                if row["id"] == c.pk:
                    row["hours"] += hours

        teachers = Course.objects.filter(org=school, teacher__isnull=False).values("teacher").distinct().count()
        staff = SchoolMembership.objects.filter(school=school).exclude(user__role__in=["teacher", "student"]).values("user").distinct().count()
        return Response({
            "school": school.name,
            "classes_total": len(classes),
            "students_total": sum(students_by_class.values()),
            "teachers": teachers,
            "staff": staff,
            "by_field": [{"field": k, **v} for k, v in sorted(by_field.items())],
            "by_grade": [{"grade": k, **v} for k, v in sorted(by_grade.items())],
            "classes": sorted(class_rows, key=lambda x: (x["grade"], x["name"])),
        })
