from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, ParentLink, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope

from .models import BASE_SCORE, DisciplineFactor, DisciplineRecord

# who records in the discipline book: the teacher, the principal and the two deputies (see RECORDER_ROLES)
RECORDER_ROLES = ("teacher", "school_admin", "school_deputy_edu", "school_deputy_exec")
READERS = role_required(*User.READ_ROLES, User.Role.STUDENT, User.Role.PARENT)


def _student_in_scope(user, student):
    if student.role != User.Role.STUDENT or student.org is None:
        raise serializers.ValidationError({"student": "دانش‌آموز نامعتبر است."})
    if not org_in_scope(user, student.org):
        raise PermissionDenied("این دانش‌آموز خارج از محدوده شماست.")


def _can_read(user, student):
    if user.role == User.Role.STUDENT:
        return user.pk == student.pk
    if user.role == User.Role.PARENT:
        return ParentLink.objects.filter(parent=user, student=student, is_verified=True).exists()
    return org_in_scope(user, student.org)


def worksheet(student):
    rows = list(DisciplineRecord.objects.filter(student=student).order_by("date", "id"))
    raw = sum((r.points for r in rows), Decimal(0))
    score = max(Decimal(0), min(Decimal(BASE_SCORE), Decimal(BASE_SCORE) + raw))
    return {"student": {"id": student.pk, "name": student.get_full_name() or student.username,
                        "student_code": student.student_code or "", "national_id": student.national_id or "",
                        "school": student.org.name if student.org_id else ""},
            "base": BASE_SCORE, "total_points": float(raw), "score": float(score),
            "records": [{"id": r.pk, "date": r.date, "title": r.title, "points": float(r.points), "note": r.note,
                         "recorded_by": (r.recorded_by.get_full_name() or r.recorded_by.username) if r.recorded_by_id else ""} for r in rows]}


class FactorsView(APIView):
    """GET ?school=<id> — the default factors plus the school's own (active ones). POST — a new factor for the school."""

    def get(self, request):
        school_id = request.query_params.get("school")
        u = request.user
        q = Q(school__isnull=True)
        if school_id and str(school_id).isdigit():
            school = get_object_or_404(OrgUnit, pk=school_id, kind="school")
            if not org_in_scope(u, school):
                raise PermissionDenied()
            q |= Q(school=school)
        elif u.role in User.SCHOOL_ROLES and u.org_id:
            q |= Q(school=u.org)
        rows = DisciplineFactor.objects.filter(q, is_active=True).order_by("points", "title")
        return Response([{"id": f.pk, "title": f.title, "points": float(f.points), "default": f.school_id is None} for f in rows])

    def post(self, request):
        u = request.user
        if u.role not in (User.Role.SCHOOL_ADMIN, User.Role.SCHOOL_DEPUTY_EDU, User.Role.SCHOOL_DEPUTY_EXEC):
            raise PermissionDenied("تعریف عامل انضباطی را فقط مدیر و معاون مدرسه انجام می‌دهد.")
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind="school")
        if not org_in_scope(u, school):
            raise PermissionDenied()
        title = str(request.data.get("title", "")).strip()[:200]
        try:
            points = Decimal(str(request.data.get("points", "0")))
        except Exception:
            raise serializers.ValidationError({"points": "عدد نامعتبر است."})
        if not title:
            raise serializers.ValidationError({"title": "نام عامل لازم است."})
        if points == 0 or abs(points) > 5:
            raise serializers.ValidationError({"points": "مقدار نمره باید غیرصفر و بین −۵ تا +۵ باشد."})
        f, created = DisciplineFactor.objects.get_or_create(school=school, title=title, defaults={"points": points, "created_by": u})
        if not created:
            raise serializers.ValidationError({"title": "این عامل برای این مدرسه قبلاً ثبت شده است."})
        audit(request, "discipline.factor", f"factor:{f.pk}")
        return Response({"id": f.pk, "title": f.title, "points": float(f.points)}, status=201)


class FactorDetailView(APIView):
    """PATCH: change points / title / active (the school's factors by its recorders; the default catalogue by the
    system administrator only). DELETE: the factor is switched off (records keep their copy of the title and points)."""

    def _get(self, request, pk):
        f = get_object_or_404(DisciplineFactor, pk=pk)
        if f.school_id is None:
            if request.user.role != User.Role.SUPER_ADMIN:
                raise PermissionDenied("عامل‌های پیش‌فرض را فقط مدیر کل تغییر می‌دهد.")
        elif not org_in_scope(request.user, f.school) or request.user.role not in (User.Role.TEACHER, User.Role.SCHOOL_ADMIN, User.Role.SCHOOL_DEPUTY_EDU, User.Role.SCHOOL_DEPUTY_EXEC, User.Role.SUPER_ADMIN, User.Role.PROVINCE_ADMIN, User.Role.COUNTY_ADMIN, User.Role.REGION_ADMIN):
            raise PermissionDenied()
        return f

    def patch(self, request, pk):
        f = self._get(request, pk)
        if "points" in request.data:
            try:
                p = Decimal(str(request.data["points"]))
            except Exception:
                raise serializers.ValidationError({"points": "عدد نامعتبر است."})
            if p == 0 or abs(p) > 5:
                raise serializers.ValidationError({"points": "مقدار نمره باید غیرصفر و بین −۵ تا +۵ باشد."})
            f.points = p
        if "title" in request.data and str(request.data["title"]).strip():
            f.title = str(request.data["title"]).strip()[:200]
        if "is_active" in request.data:
            f.is_active = bool(request.data["is_active"])
        f.save()
        audit(request, "discipline.factor.update", f"factor:{f.pk}")
        return Response({"id": f.pk, "title": f.title, "points": float(f.points), "is_active": f.is_active})

    def delete(self, request, pk):
        f = self._get(request, pk)
        f.is_active = False
        f.save(update_fields=["is_active"])
        audit(request, "discipline.factor.off", f"factor:{f.pk}")
        return Response(status=204)


class RecordsView(APIView):
    """GET ?student=<id> — the student's discipline book. POST — a new entry (points are copied from the factor)."""

    def get(self, request):
        sid = request.query_params.get("student")
        if not str(sid or "").isdigit():
            raise serializers.ValidationError({"student": "دانش‌آموز را انتخاب کنید."})
        student = get_object_or_404(User, pk=sid)
        if not _can_read(request.user, student):
            raise PermissionDenied()
        return Response(worksheet(student))

    def post(self, request):
        if request.user.role not in (User.Role.TEACHER, User.Role.SCHOOL_ADMIN, User.Role.SCHOOL_DEPUTY_EDU, User.Role.SCHOOL_DEPUTY_EXEC):
            raise PermissionDenied()
        student = get_object_or_404(User, pk=request.data.get("student") or 0)
        _student_in_scope(request.user, student)
        if request.user.role == User.Role.TEACHER:
            from datetime import date as _date
            from .teacher_rules import check_teacher_record
            check_teacher_record(request.user, student, _date.fromisoformat(str(request.data.get("date") or timezone.localdate().isoformat())[:10]))
        factor = get_object_or_404(DisciplineFactor, pk=request.data.get("factor") or 0, is_active=True)
        if factor.school_id not in (None, student.org_id):
            raise serializers.ValidationError({"factor": "این عامل برای مدرسه این دانش‌آموز نیست."})
        date = request.data.get("date") or timezone.localdate().isoformat()
        rec = DisciplineRecord.objects.create(student=student, school=student.org, factor=factor, title=factor.title,
                                              points=factor.points, date=date, note=str(request.data.get("note", ""))[:300],
                                              recorded_by=request.user)
        audit(request, "discipline.record", f"student:{student.pk} record:{rec.pk}")
        return Response(worksheet(student), status=201)


class RecordDetailView(APIView):
    """DELETE — the entry is removed (by the person who recorded it or by the principal / deputies of the school)."""

    def _can(self, u, rec):
        from .teacher_rules import can_teacher_change
        if u.role == User.Role.TEACHER:
            return can_teacher_change(u, rec)
        return org_in_scope(u, rec.school) and (u.pk == rec.recorded_by_id or u.role in (User.Role.SCHOOL_ADMIN, User.Role.SCHOOL_DEPUTY_EDU, User.Role.SCHOOL_DEPUTY_EXEC))

    def patch(self, request, pk):
        """ویرایش عامل، تاریخ یا توضیح یک مورد (دبیر تا قبل از چاپ کارنامه‌ی انضباطی)."""
        rec = get_object_or_404(DisciplineRecord, pk=pk)
        u = request.user
        if not self._can(u, rec):
            raise PermissionDenied("این مورد قابل ویرایش نیست (کارنامه‌ی انضباطی چاپ شده یا خارج از کلاس شماست).")
        if "factor" in request.data:
            factor = get_object_or_404(DisciplineFactor, pk=request.data.get("factor") or 0, is_active=True)
            rec.factor, rec.title, rec.points = factor, factor.title, factor.points
        if request.data.get("date"):
            from datetime import date as _date
            day = _date.fromisoformat(str(request.data["date"])[:10])
            if u.role == User.Role.TEACHER:
                from .teacher_rules import present_on
                if not present_on(u, day):
                    raise serializers.ValidationError({"date": "این تاریخ در ساعت حضور شما نیست."})
            rec.date = day
        if "note" in request.data:
            rec.note = str(request.data.get("note") or "")[:300]
        rec.save()
        audit(request, "discipline.record.edit", f"student:{rec.student_id} record:{pk}")
        return Response(worksheet(rec.student))

    def delete(self, request, pk):
        rec = get_object_or_404(DisciplineRecord, pk=pk)
        u = request.user
        if not self._can(u, rec):
            raise PermissionDenied("این مورد قابل حذف نیست (کارنامه‌ی انضباطی چاپ شده یا خارج از کلاس شماست).")
        student = rec.student
        rec.delete()
        audit(request, "discipline.record.delete", f"student:{student.pk} record:{pk}")
        return Response(worksheet(student))


class WorksheetView(APIView):
    """GET /api/discipline/worksheet/<student id>/ — the discipline worksheet (کاربرگ انضباطی) for printing."""
    permission_classes = [READERS]

    def get(self, request, pk):
        student = get_object_or_404(User, pk=pk, role=User.Role.STUDENT)
        if not _can_read(request.user, student):
            raise PermissionDenied()
        return Response(worksheet(student))


class RecordsBulkView(APIView):
    """POST {students:[ids], factor, date, note} — یک عامل انضباطی برای چند دانش‌آموز (همان قواعد ثبت تکی)."""

    def post(self, request):
        u = request.user
        if u.role not in (User.Role.TEACHER, User.Role.SCHOOL_ADMIN, User.Role.SCHOOL_DEPUTY_EDU, User.Role.SCHOOL_DEPUTY_EXEC):
            raise PermissionDenied()
        ids = [int(x) for x in (request.data.get("students") or []) if str(x).isdigit()]
        if not ids:
            raise serializers.ValidationError({"students": "حداقل یک دانش‌آموز را انتخاب کنید."})
        factor = get_object_or_404(DisciplineFactor, pk=request.data.get("factor") or 0, is_active=True)
        date = request.data.get("date") or timezone.localdate().isoformat()
        note = str(request.data.get("note", ""))[:300]
        made, skipped = 0, []
        from datetime import date as _date
        from .teacher_rules import check_teacher_record
        day = _date.fromisoformat(str(date)[:10])
        for student in User.objects.filter(pk__in=ids, role=User.Role.STUDENT):
            try:
                _student_in_scope(u, student)
                if u.role == User.Role.TEACHER:
                    check_teacher_record(u, student, day)
            except (PermissionDenied, ValidationError):
                skipped.append(student.pk)
                continue
            if factor.school_id not in (None, student.org_id):
                skipped.append(student.pk)
                continue
            DisciplineRecord.objects.create(student=student, school=student.org, factor=factor, title=factor.title,
                                            points=factor.points, date=date, note=note, recorded_by=u)
            made += 1
        audit(request, "discipline.record.bulk", f"factor:{factor.pk} made:{made}")
        return Response({"ok": True, "made": made, "skipped": skipped})

