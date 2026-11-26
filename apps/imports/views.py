from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope, scope_q

from .models import ImportJob
from .parsers import KIND_LABELS, parse
from .rules import NoCurrentYear, commit, validate

IsAdmin = role_required(*User.ADMIN_ROLES)
MAX_UPLOAD = 5 * 1024 * 1024


class ImportView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


def _job_dict(job):
    from .rules import missing_classes
    rows = job.rows
    return {"id": job.pk, "kind": job.kind, "kind_label": KIND_LABELS.get(job.kind, job.kind),
            "school": job.school.name, "school_id": job.school_id, "source": job.source_name,
            "status": job.status, "unknown_headers": job.unknown_headers, "rows": rows,
            "summary": {"total": len(rows), "ok": sum(1 for r in rows if r["status"] == "ok"),
                        "error": sum(1 for r in rows if r["status"] == "error"), **job.summary},
            "missing_classes": missing_classes(rows) if job.kind == "students" else []}


def _visible(user):
    qs = ImportJob.objects.select_related("school")
    return qs if user.role == User.Role.SUPER_ADMIN else qs.filter(created_by=user)


class ImportCreateView(ImportView):
    """POST /api/imports/ (multipart: kind, school, file | text). Returns the preview: every row green (ok) or red."""
    permission_classes = [IsAdmin]

    def post(self, request):
        u = request.user
        kind = request.data.get("kind", "")
        school = get_object_or_404(OrgUnit, pk=request.data.get("school") or 0, kind=OrgUnit.Kind.SCHOOL)
        if not org_in_scope(u, school):
            return Response(status=404)
        f = request.FILES.get("file")
        text = request.data.get("text")
        if kind not in ('students', 'classes', 'teachers', 'courses', 'subjects', 'class_teachers', 'class_students', 'staff', 'fields', 'curriculum'):
            raise serializers.ValidationError({'kind': 'نوع ورود نامعتبر است.'})
        if kind == 'subjects' and u.role not in (User.Role.SUPER_ADMIN, User.Role.PROVINCE_ADMIN):
            raise PermissionDenied('سرفصل درس‌ها (کاتالوگ) فقط توسط مدیر کل یا مدیر استان وارد می‌شود.')
        if (f is None) == (text is None):
            raise serializers.ValidationError({"file": "یکی از «فایل» یا «متن چسبانده‌شده» را بفرستید."})
        if f is not None and f.size > MAX_UPLOAD:
            raise serializers.ValidationError({"file": "حجم فایل بیش از ۵ مگابایت است."})
        try:
            if f is not None:
                rows, unknown = parse(kind, name=f.name, data=f.read())
                source = f.name[:200]
            else:
                rows, unknown = parse(kind, text=str(text)[:500_000])
                source = "متن چسبانده‌شده"
            checked = validate(kind, school, rows)
        except (ValueError, NoCurrentYear) as e:
            raise serializers.ValidationError({"file": str(e)})
        except Exception:                                    # unexpected: log the detail, tell the user what to do
            import logging
            logging.getLogger("lms.imports").exception("import preview failed")
            raise serializers.ValidationError({"file": "پردازش این متن ممکن نشد. ساختار جدول (سرستون‌ها) را بررسی کنید؛ "
                                                       "اگر مشکل ادامه داشت، همان جدول را با Excel بفرستید."})
        job = ImportJob.objects.create(created_by=u, school=school, kind=kind, source_name=source,
                                       rows=checked, unknown_headers=unknown)
        audit(request, "import.preview", f"import:{job.pk} {kind} n={len(checked)}")
        return Response(_job_dict(job), status=201)


class ImportDetailView(ImportView):
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        return Response(_job_dict(get_object_or_404(_visible(request.user), pk=pk)))


class RowsIn(serializers.Serializer):
    rows = serializers.ListField(child=serializers.DictField(), max_length=5000)


class ImportRowsView(ImportView):
    """PATCH: the user corrects cells. Values are merged per row and the WHOLE job is re-validated."""
    permission_classes = [IsAdmin]

    def patch(self, request, pk):
        job = get_object_or_404(_visible(request.user), pk=pk)
        if job.status != ImportJob.Status.PREVIEW:
            return Response({"detail": "این ورود قبلاً ثبت شده است."}, status=409)
        s = RowsIn(data=request.data)
        s.is_valid(raise_exception=True)
        edits = {}
        for e in s.validated_data["rows"]:
            try:
                edits[int(e.get("n"))] = e.get("values") or {}
            except (TypeError, ValueError):
                raise serializers.ValidationError({"rows": "شماره ردیف نامعتبر است."})
        base = [{"n": r["n"], "values": {**r["values"], **{k: str(v)[:300] for k, v in edits.get(r["n"], {}).items()
                                                            if k in r["values"]}}} for r in job.rows]
        job.rows = validate(job.kind, job.school, base)
        job.save(update_fields=["rows"])
        return Response(_job_dict(job))


class ImportCommitView(ImportView):
    """POST: writes the GREEN rows only, after re-validating. Red rows stay unsaved."""
    permission_classes = [IsAdmin]

    def post(self, request, pk):
        job = get_object_or_404(_visible(request.user), pk=pk)
        if job.status != ImportJob.Status.PREVIEW:
            return Response({"detail": "این ورود قبلاً ثبت شده است."}, status=409)
        base = [{"n": r["n"], "values": r["values"]} for r in job.rows]
        extra = {}
        if job.kind == "students" and request.data.get("create_classes") in (True, "true", "1", 1):
            from apps.academics.models import AcademicYear
            from .rules import create_missing_classes, missing_classes
            year = AcademicYear.objects.filter(is_current=True).first()
            if year is None:
                return Response({"detail": "سال تحصیلی جاری تعریف نشده است."}, status=400)
            made, skipped = create_missing_classes(job.school, year, missing_classes(job.rows))
            extra = {"classes_created": made, "classes_skipped": skipped}
            audit(request, "import.classes", f"import:{job.pk} created={made}")
        rows = validate(job.kind, job.school, base)
        try:
            created, failed = commit(job.kind, job.school, rows)
        except Exception:
            import logging
            logging.getLogger("lms.imports").exception("import commit failed")
            return Response({"detail": "ثبت ردیف‌ها ممکن نشد؛ پیش‌نمایش را بررسی و دوباره امتحان کنید."}, status=400)
        with transaction.atomic():
            job.rows = rows
            job.status = ImportJob.Status.COMMITTED
            job.committed_at = timezone.now()
            job.summary = {"created": len(created), "failed": failed}
            job.save(update_fields=["rows", "status", "committed_at", "summary"])
        audit(request, "import.commit", f"import:{job.pk} created={len(created)}")
        return Response({**_job_dict(job), **extra})


class ImportListView(ImportView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response([{"id": j.pk, "kind": j.kind, "school": j.school.name, "status": j.status,
                          "source": j.source_name, "created_at": j.created_at}
                         for j in _visible(request.user).order_by("-created_at")[:50]])


class MySchoolsView(APIView):
    """The school 'kartabl' list: every school the user may act on (principals/teachers: their memberships)."""

    def get(self, request):
        u = request.user
        if u.role in (User.Role.STUDENT, User.Role.PARENT):
            return Response([])
        if u.role == User.Role.TEACHER:
            # a teacher's schools are the schools where he teaches a class, the one with the most lessons first
            from django.db.models import Count as _Count
            from apps.courses.models import Course as _Course
            taught = (_Course.objects.filter(teacher=u, org__kind=OrgUnit.Kind.SCHOOL)
                      .values("org_id").annotate(n=_Count("id")).order_by("-n"))
            order = [t["org_id"] for t in taught]
            schools = list(OrgUnit.objects.filter(pk__in=order).select_related("parent"))
            schools.sort(key=lambda x: order.index(x.pk))
            from apps.accounts.models import SchoolProfile as _SP
            profs = {p.school_id: p for p in _SP.objects.select_related("level").filter(school__in=schools)}
            return Response([{"id": s.pk, "name": s.name, "code": profs[s.pk].school_code if s.pk in profs else "",
                              "level": profs[s.pk].level.name if s.pk in profs else "",
                              "level_id": profs[s.pk].level_id if s.pk in profs else None,
                              "region": s.parent.name if s.parent_id else ""} for s in schools])
        qs = OrgUnit.objects.filter(kind=OrgUnit.Kind.SCHOOL).filter(scope_q(u, "path")).select_related("parent")
        from apps.accounts.models import SchoolProfile
        profiles = {p.school_id: p for p in SchoolProfile.objects.select_related("level").filter(school__in=qs)}
        out = []
        from django.db.models import Count
        for s in qs.annotate(n_users=Count("memberships")).order_by("-n_users", "name")[:300]:
            p = profiles.get(s.pk)
            out.append({"id": s.pk, "name": s.name, "code": p.school_code if p else "",
                        "level": p.level.name if p else "", "level_id": p.level_id if p else None,
                        "region": s.parent.name if s.parent_id else ""})
        return Response(out)
