from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit, record_grade_change
from apps.core.permissions import role_required
from apps.core.scope import org_scope_q
from apps.courses.models import Enrollment
from apps.exams.authoring import staff_courses
from apps.files.models import FileAsset

from .models import Assignment, Submission, validate_https_url

STAFF = role_required(*User.STAFF_ROLES)
IsStudent = role_required(User.Role.STUDENT)


class CourseworkView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


def staff_assignments(user):
    qs = Assignment.objects.all()
    if user.role == User.Role.TEACHER:
        return qs.filter(course__teacher=user)
    return qs.filter(org_scope_q(user, "course__org"))


def teacher_assignments(user):
    """Submissions are private: only the course teacher (not school/region admins) may read and grade them."""
    return Assignment.objects.filter(course__teacher=user)


def student_assignments(user):
    now = timezone.now()
    return (Assignment.objects.filter(is_published=True, course__enrollments__student=user)
            .filter(Q(audience="all") | Q(targets=user)).filter(Q(opens_at__isnull=True) | Q(opens_at__lte=now)).distinct())


def _files(qs):
    return [f.as_dict() for f in qs.all()]


class AssignmentListView(CourseworkView):
    def get(self, request):
        from apps.core.activity import record_view
        u = request.user
        if u.role == User.Role.STUDENT:
            record_view(request, "assignment.view", f"user:{u.pk}")
        qs = Assignment.objects.select_related("course")
        if u.role == User.Role.STUDENT:
            qs = student_assignments(u).select_related("course")
        elif u.role in User.STAFF_ROLES:
            qs = staff_assignments(u).select_related("course")
        else:
            qs = qs.none()
        qs = qs.prefetch_related("attachments")
        from apps.core.scope import kartabl_filter
        qs = qs.filter(**kartabl_filter(request, "course__org_id"))
        if request.query_params.get("course", "").isdigit():
            qs = qs.filter(course_id=int(request.query_params["course"]))
        items = list(qs.order_by("-due_at")[:200])
        mine = {}
        if u.role == User.Role.STUDENT and items:
            mine = {s.assignment_id: s for s in Submission.objects.filter(student=u, assignment__in=items).prefetch_related("files")}
        out = []
        for a in items:
            row = {"id": a.pk, "course": a.course_id, "course_title": a.course.title, "title": a.title,
                   "description": a.description, "due_at": a.due_at, "max_points": float(a.max_points),
                   "is_published": a.is_published, "allow_late": a.allow_late, "opens_at": a.opens_at,
                   "audience": a.audience, "attachments": _files(a.attachments)}
            if u.role == User.Role.STUDENT:
                s = mine.get(a.pk)
                row["submission"] = None if s is None else {
                    "submitted_at": s.submitted_at, "is_late": s.is_late, "text": s.text, "file_url": s.file_url,
                    "files": _files(s.files),
                    "points": None if s.points is None else float(s.points), "feedback": s.feedback}
            out.append(row)
        return Response(out)


class AssignmentIn(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    max_points = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=1, max_value=100, default=20)
    allow_late = serializers.BooleanField(default=False)
    opens_at = serializers.DateTimeField(required=False, allow_null=True)
    audience = serializers.ChoiceField(choices=["all", "selected"], default="all")
    students = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=500)
    attachments = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=10)

    def validate(self, d):
        from apps.core.htmlclean import clean_html
        d["description"] = clean_html(d.get("description", ""))[0]        # teacher rich text: sanitised like content
        if d["audience"] == "selected" and not d.get("students"):
            raise serializers.ValidationError({"students": "حداقل یک دانش‌آموز انتخاب کنید."})
        if d.get("opens_at") and d.get("due_at") and d["opens_at"] >= d["due_at"]:
            raise serializers.ValidationError({"opens_at": "شروع ارسال باید قبل از مهلت باشد."})
        return d


class AssignmentCreateView(CourseworkView):
    permission_classes = [STAFF]

    def post(self, request, course_id):
        course = get_object_or_404(staff_courses(request.user), pk=course_id)
        s = AssignmentIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = dict(s.validated_data)
        student_ids, file_ids = set(d.pop("students", [])), set(d.pop("attachments", []))
        if student_ids and Enrollment.objects.filter(course=course, student_id__in=student_ids).count() != len(student_ids):
            raise serializers.ValidationError({"students": "برخی دانش‌آموزان در این درس ثبت‌نام نیستند."})
        files = list(FileAsset.objects.filter(pk__in=file_ids, owner=request.user))
        if len(files) != len(file_ids):
            raise serializers.ValidationError({"attachments": "فایل نامعتبر است."})
        with transaction.atomic():
            a = Assignment.objects.create(course=course, created_by=request.user, **d)
            if d["audience"] == "selected":
                a.targets.set(student_ids)
            a.attachments.set(files)
        audit(request, "assignment.create", f"assignment:{a.pk}")
        return Response({"id": a.pk}, status=201)


class AssignmentPatch(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    due_at = serializers.DateTimeField(required=False)
    allow_late = serializers.BooleanField(required=False)
    is_published = serializers.BooleanField(required=False)


class AssignmentManageView(CourseworkView):
    permission_classes = [STAFF]

    def patch(self, request, pk):
        a = get_object_or_404(staff_assignments(request.user), pk=pk)
        s = AssignmentPatch(data=request.data)
        s.is_valid(raise_exception=True)
        for k, v in s.validated_data.items():
            setattr(a, k, v)
        a.save()
        audit(request, "assignment.update", f"assignment:{a.pk}")
        return Response({"id": a.pk, "is_published": a.is_published})


class SubmitInput(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    file_url = serializers.CharField(required=False, allow_blank=True, max_length=500)
    files = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=5)

    def validate(self, d):
        if not (d.get("text", "").strip() or d.get("file_url", "").strip() or d.get("files")):
            raise serializers.ValidationError("متن، فایل یا پیوند لازم است.")
        if d.get("file_url"):
            try:
                validate_https_url(d["file_url"])
            except Exception:
                raise serializers.ValidationError({"file_url": "آدرس معتبر با https وارد کنید."})
        return d


class SubmitAssignmentView(CourseworkView):
    """POST /api/assignments/<id>/submit/ — re-submitting before grading replaces the previous answer."""
    permission_classes = [IsStudent]

    def post(self, request, pk):
        u = request.user
        a = get_object_or_404(student_assignments(u), pk=pk)
        s = SubmitInput(data=request.data)
        s.is_valid(raise_exception=True)
        data = dict(s.validated_data)
        from apps.core.htmlclean import clean_html
        if data.get("text"):
            data["text"] = clean_html(data["text"])[0]            # the answer keeps bold / italic / lists, cleaned
        file_ids = set(data.pop("files", []))
        files = list(FileAsset.objects.filter(pk__in=file_ids, owner=u))
        if len(files) != len(file_ids):
            raise serializers.ValidationError({"files": "فایل نامعتبر است."})
        now = timezone.now()
        late = bool(a.due_at) and now > a.due_at
        if late and not a.allow_late:
            raise PermissionDenied("مهلت ارسال تکلیف به پایان رسیده است.")
        with transaction.atomic():
            sub, created = Submission.objects.select_for_update().get_or_create(
                assignment=a, student=u, defaults={"submitted_at": now, "is_late": late, **data})
            if not created:
                if sub.graded_at is not None:
                    return Response({"detail": "تکلیف تصحیح شده و قابل تغییر نیست."}, status=409)
                sub.text, sub.file_url = data.get("text", ""), data.get("file_url", "")
                sub.submitted_at, sub.is_late = now, late
                sub.save(update_fields=["text", "file_url", "submitted_at", "is_late"])
            sub.files.set(files)
        audit(request, "assignment.submit", f"assignment:{a.pk}")
        return Response({"submitted_at": sub.submitted_at, "is_late": sub.is_late}, status=201 if created else 200)


class SubmissionListView(CourseworkView):
    permission_classes = [STAFF]

    def get(self, request, pk):
        a = get_object_or_404(teacher_assignments(request.user), pk=pk)
        rows = (Submission.objects.filter(assignment=a).select_related("student").prefetch_related("files")
                .order_by("student__username")[:500])
        return Response([{"id": r.pk, "student": r.student.username, "student_name": r.student.get_full_name(),
                          "text": r.text, "file_url": r.file_url, "submitted_at": r.submitted_at,
                          "is_late": r.is_late, "points": None if r.points is None else float(r.points),
                          "feedback": r.feedback, "files": _files(r.files)} for r in rows])


class GradeInput(serializers.Serializer):
    points = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0)
    feedback = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class GradeSubmissionView(CourseworkView):
    """Changing an existing grade requires a reason; every change is written to the grade history."""
    permission_classes = [STAFF]

    def post(self, request, pk):
        s = GradeInput(data=request.data)
        s.is_valid(raise_exception=True)
        points, reason = s.validated_data["points"], s.validated_data.get("reason", "").strip()
        with transaction.atomic():
            sub = get_object_or_404(
                Submission.objects.select_for_update(of=("self",)).select_related("assignment"), pk=pk,
                assignment__in=teacher_assignments(request.user))
            if points > sub.assignment.max_points:
                raise serializers.ValidationError({"points": f"حداکثر نمره {sub.assignment.max_points} است."})
            old = sub.points
            if old is not None and old != points and not reason:
                raise serializers.ValidationError({"reason": "برای تغییر نمره، ذکر دلیل الزامی است."})
            if old != points:
                record_grade_change(request, "submission", sub.pk, sub.student_id, old, points, reason)
            sub.points, sub.graded_by, sub.graded_at = points, request.user, timezone.now()
            sub.feedback = s.validated_data.get("feedback", sub.feedback)
            sub.save(update_fields=["points", "graded_by", "graded_at", "feedback"])
        audit(request, "grade.submission", f"submission:{sub.pk}")
        return Response({"id": sub.pk, "points": float(sub.points or Decimal(0))})
