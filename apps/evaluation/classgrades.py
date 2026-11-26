"""نمره سرکلاسی and the class grade worksheet (کاربرگ نمرات کلاس) with the rank of every student in the class."""
from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.academics.models import ClassRoom
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.courses.models import Course

from .logic import course_percentages
from .models import ClassGrade
from .ranking import competition_ranks

GRADERS = ("teacher", "school_admin", "school_deputy_edu", "school_deputy_exec")


def _course_for_write(user, course):
    if user.role == User.Role.TEACHER:
        if course.teacher_id != user.pk:
            raise PermissionDenied("فقط معلم همین درس می‌تواند نمره سرکلاسی ثبت کند.")
    elif user.role not in GRADERS or not org_in_scope(user, course.org):
        raise PermissionDenied()


class ClassGradeBulkView(APIView):
    """POST {course, title, date, max_score, rows:[{student, score}]} — one class-mark for many students at once."""

    def post(self, request):
        from datetime import date as _d
        course = get_object_or_404(Course, pk=request.data.get("course") or 0)
        _course_for_write(request.user, course)
        title = str(request.data.get("title", "")).strip()[:200]
        if not title:
            raise serializers.ValidationError({"title": "عنوان نمره سرکلاسی لازم است."})
        try:
            maxs = Decimal(str(request.data.get("max_score", "20")))
            day = _d.fromisoformat(str(request.data.get("date") or ""))
        except (ValueError, ArithmeticError):
            raise serializers.ValidationError({"date": "تاریخ یا بیشترین نمره نامعتبر است."})
        if not (Decimal("0") < maxs <= Decimal("100")):
            raise serializers.ValidationError({"max_score": "بیشترین نمره باید بین ۰ تا ۱۰۰ باشد."})
        from apps.courses.roster import roster_ids
        enrolled = roster_ids(course)
        saved, errors = 0, []
        for row in request.data.get("rows") or []:
            try:
                sid = int(row.get("student"))
                score = Decimal(str(row.get("score")))
            except (TypeError, ValueError, ArithmeticError):
                errors.append({"student": row.get("student"), "reason": "ورودی نامعتبر"})
                continue
            if sid not in enrolled:
                errors.append({"student": sid, "reason": "در این درس ثبت‌نام نیست"})
                continue
            from apps.exams.score_review import grade_book_block
            blocked = grade_book_block(course, title, request.data.get("period") or "", sid)
            if blocked:
                errors.append({"student": sid, "reason": blocked})
                continue
            if score < 0 or score > maxs:
                errors.append({"student": sid, "reason": "نمره بیشتر از بیشترین نمره است"})
                continue
            period = request.data.get("period") if request.data.get("period") in dict(ClassGrade.PERIOD) else "class"
            from apps.exams.score_review import grade_book_changed
            grade_book_changed(course, title, request.data.get("period") or "", sid)
            ClassGrade.objects.create(student_id=sid, course=course, title=title, date=day, score=score, max_score=maxs,
                                      period=period, recorded_by=request.user)
            saved += 1
        audit(request, "classgrade.bulk", f"course:{course.pk} saved={saved}")
        return Response({"saved": saved, "errors": errors}, status=201)


class ClassGradeDeleteView(APIView):
    def delete(self, request, pk):
        g = get_object_or_404(ClassGrade, pk=pk)
        _course_for_write(request.user, g.course)
        from apps.exams.score_review import grade_book_block
        blocked = grade_book_block(g.course, g.title, g.period, g.student_id)
        if blocked:
            raise serializers.ValidationError({"detail": blocked})
        g.delete()
        audit(request, "classgrade.delete", f"classgrade:{pk}")
        return Response(status=204)


def class_worksheet(classroom):
    """Every student of the class: his mark in each course (exams, homework and class marks together, in %),
    his overall average and his rank in the class (equal marks share the rank)."""
    school_id = classroom.school_id
    from apps.courses.models import Enrollment
    member_ids = set(classroom.students.filter(role="student").values_list("pk", flat=True))
    member_ids |= set(Enrollment.objects.filter(course__classroom=classroom, student__role="student").values_list("student_id", flat=True))
    students = list(User.objects.filter(pk__in=member_ids).order_by("last_name", "username"))
    ids = [s.pk for s in students]
    courses = list(Course.objects.filter(classroom=classroom).order_by("title"))
    cids = [c.pk for c in courses]
    pct = course_percentages([school_id], student_ids=ids, course_ids=cids)
    from .models import GraceAward
    grace = {}
    for a in GraceAward.objects.filter(student_id__in=ids, course_id__in=cids):
        grace[(a.student_id, a.course_id)] = grace.get((a.student_id, a.course_id), 0) + float(a.points)
    cg = {}
    for g in ClassGrade.objects.filter(student_id__in=ids, course_id__in=cids):
        # every mark is shown on 20: score ÷ its total × 20 (e.g. 2 of 5 → 8)
        cg.setdefault((g.student_id, g.course_id), []).append(float(g.score) / float(g.max_score) * 20)
    rows = []
    for s in students:
        per = {}
        for c in courses:
            items = []
            rec = pct.get((s.pk, c.pk))
            if rec:
                items += [x * 0.2 for x in rec[2] + rec[3]]      # the percent items of the course, converted to 20
            items += cg.get((s.pk, c.pk), [])
            if items:
                base = sum(items) / len(items) + grace.get((s.pk, c.pk), 0)      # ارفاق (out of 20) is added to the course mark
                per[c.pk] = round(min(20.0, base), 2)
        overall = round(sum(per.values()) / len(per), 2) if per else None
        rows.append({"student": s.pk, "name": s.get_full_name() or s.username, "student_code": s.student_code or "",
                     "courses": {str(k): v for k, v in per.items()}, "overall": overall})
    ranks = competition_ranks({r["student"]: r["overall"] for r in rows})
    for r in rows:
        r["rank"] = ranks.get(r["student"])
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, r["name"]))
    return {"classroom": classroom.name, "school": classroom.school.name, "grace_points": sum(grace.values()),
            "courses": [{"id": c.pk, "title": c.title} for c in courses], "rows": rows}


class ClassWorksheetView(APIView):
    """GET /api/classes/<id>/worksheet/ — the class grade worksheet (کاربرگ نمرات کلاس) with ranks."""

    def get(self, request, pk):
        classroom = get_object_or_404(ClassRoom.objects.select_related("school"), pk=pk)
        u = request.user
        if u.role == User.Role.TEACHER:
            if not Course.objects.filter(classroom=classroom, teacher=u).exists() and not classroom.students.exists():
                raise PermissionDenied()
        elif u.role not in User.READ_ROLES or not org_in_scope(u, classroom.school):
            raise PermissionDenied()
        data = class_worksheet(classroom)
        if request.query_params.get("format") == "csv":
            import csv, io
            from django.http import HttpResponse
            buf = io.StringIO(); buf.write("\ufeff"); w = csv.writer(buf)
            w.writerow(["رتبه", "نام", "کد دانش‌آموز"] + [c["title"] for c in data["courses"]] + ["میانگین"])
            for r in data["rows"]:
                w.writerow([r["rank"] or "", r["name"], r["student_code"]] + [r["courses"].get(str(c["id"]), "") for c in data["courses"]] + [r["overall"] or ""])
            resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="class-{classroom.pk}.csv"'
            return resp
        return Response(data)


class GraceBulkView(APIView):
    """POST {course, students:[ids], points, reason} — ارفاق for many students of a course at once."""

    def post(self, request):
        course = get_object_or_404(Course, pk=request.data.get("course") or 0)
        _course_for_write(request.user, course)
        try:
            pts = Decimal(str(request.data.get("points")))
        except (TypeError, ArithmeticError):
            raise serializers.ValidationError({"points": "امتیاز ارفاق نامعتبر است."})
        if not (Decimal("0") < pts <= Decimal("20")):
            raise serializers.ValidationError({"points": "ارفاق باید بین ۰ تا ۲۰ امتیاز باشد."})
        from apps.courses.roster import roster_ids
        enrolled = roster_ids(course)
        from .models import GraceAward
        made = 0
        for sid in request.data.get("students") or []:
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            if sid in enrolled:
                reason = str(request.data.get("reason", ""))[:200]
                # ارفاق تکراری (دو بار زدن دکمه) دوباره ثبت نمی‌شود
                if GraceAward.objects.filter(student_id=sid, course=course, points=pts, reason=reason).exists():
                    continue
                GraceAward.objects.create(student_id=sid, course=course, points=pts, reason=reason, created_by=request.user)
                made += 1
        audit(request, "grace.bulk", f"course:{course.pk} n={made}")
        return Response({"applied": made}, status=201)
