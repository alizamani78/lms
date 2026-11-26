from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.academics.models import ClassRoom
from apps.accounts.models import OrgUnit, ParentLink, User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import scope_q

from .models import TransferEvent, TransferRequest
from .services import execute, in_scope

S = TransferRequest.Status
IsAdmin = role_required(*User.ADMIN_ROLES)


class TransferView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


def _log(t, actor, action, note=""):
    TransferEvent.objects.create(transfer=t, actor=actor, action=action, note=note[:500])


def _row(t):
    return {"id": t.pk, "student": t.student_id, "username": t.student.username, "name": t.student.get_full_name(),
            "from_school": t.from_school.name, "to_school": t.to_school.name, "from_school_id": t.from_school_id,
            "to_school_id": t.to_school_id, "to_classroom": t.to_classroom_id,
            "to_classroom_name": t.to_classroom.name if t.to_classroom_id else "", "reason": t.reason,
            "status": t.status, "status_label": t.get_status_display(), "created_at": t.created_at,
            "summary": t.summary,
            "events": [{"action": e.action, "note": e.note, "at": e.at,
                        "actor": (e.actor.get_full_name() or e.actor.username) if e.actor else ""} for e in t.events.all()]}


def visible(user):
    qs = TransferRequest.objects.select_related("student", "from_school", "to_school", "to_classroom") \
        .prefetch_related("events__actor")
    if user.role == User.Role.SUPER_ADMIN:
        return qs
    if user.role in User.ADMIN_ROLES:
        return qs.filter(scope_q(user, "from_school__path") | scope_q(user, "to_school__path"))
    if user.role == User.Role.PARENT:
        return qs.filter(student__in=ParentLink.objects.filter(parent=user, is_verified=True).values("student"))
    return qs.none()


class CreateIn(serializers.Serializer):
    student = serializers.IntegerField()
    to_school = serializers.IntegerField()
    to_classroom = serializers.IntegerField(required=False, allow_null=True)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


def _classroom(school, pk):
    c = ClassRoom.objects.filter(pk=pk, school=school, academic_year__is_current=True).first()
    if c is None:
        raise serializers.ValidationError({"to_classroom": "کلاس باید از مدرسه مقصد و در سال تحصیلی جاری باشد."})
    return c


class TransferListCreateView(TransferView):
    def get(self, request):
        qs = visible(request.user)
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([_row(t) for t in qs.order_by("-created_at")[:200]])

    def post(self, request):
        u = request.user
        if u.role not in User.ADMIN_ROLES and u.role != User.Role.PARENT:
            raise PermissionDenied()
        s = CreateIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        student = get_object_or_404(User.objects.select_related("org"), pk=d["student"], role=User.Role.STUDENT, is_active=True)
        to_school = get_object_or_404(OrgUnit, pk=d["to_school"], kind=OrgUnit.Kind.SCHOOL)
        if student.org is None or student.org.kind != OrgUnit.Kind.SCHOOL:
            raise serializers.ValidationError({"student": "دانش‌آموز به مدرسه‌ای وصل نیست."})
        src = student.org
        if u.role == User.Role.PARENT:
            if not ParentLink.objects.filter(parent=u, student=student, is_verified=True).exists():
                return Response(status=404)
            if src.pk == to_school.pk:
                raise PermissionDenied()
        elif not (in_scope(u, src) or in_scope(u, to_school)):
            return Response(status=404)
        same = src.pk == to_school.pk
        if same and not d.get("to_classroom"):
            raise serializers.ValidationError({"to_classroom": "برای جابه‌جایی بین کلاس‌ها، کلاس مقصد لازم است."})
        classroom = _classroom(to_school, d["to_classroom"]) if d.get("to_classroom") else None
        try:
            with transaction.atomic():
                t = TransferRequest.objects.create(student=student, from_school=src, to_school=to_school,
                                                   to_classroom=classroom, reason=d.get("reason", ""), requested_by=u)
                _log(t, u, "requested", t.reason)
                # an admin who covers a side implicitly approves it
                if u.role != User.Role.PARENT and in_scope(u, src):
                    t.status = S.PENDING_DEST
                    _log(t, u, "released", "ثبت‌کننده در محدوده مدرسه مبدأ است")
                    if in_scope(u, to_school):
                        t.status = S.READY
                        _log(t, u, "accepted", "ثبت‌کننده در محدوده مدرسه مقصد است")
                t.save(update_fields=["status"])
        except IntegrityError:
            return Response({"detail": "برای این دانش‌آموز درخواست انتقال فعالی وجود دارد."}, status=409)
        audit(request, "transfer.request", f"transfer:{t.pk}")
        return Response(_row(TransferRequest.objects.get(pk=t.pk)), status=201)


class DecisionIn(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approve", "reject"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    to_classroom = serializers.IntegerField(required=False, allow_null=True)


class StepView(TransferView):
    """Shared state machine step. Subclasses define who may act and which statuses they act on."""
    permission_classes = [IsAdmin]
    from_status = None
    side = None            # "from" | "to"
    approve_to = None
    action = ""

    def post(self, request, pk):
        s = DecisionIn(data=request.data)
        s.is_valid(raise_exception=True)
        d, u = s.validated_data, request.user
        with transaction.atomic():
            t = get_object_or_404(TransferRequest.objects.select_for_update(of=("self",))
                                  .select_related("student", "from_school", "to_school", "to_classroom"), pk=pk)
            school = t.from_school if self.side == "from" else t.to_school
            if not in_scope(u, school):
                return Response(status=404)
            if t.status != self.from_status:
                return Response({"detail": "این درخواست در این مرحله نیست."}, status=409)
            if d["decision"] == "reject":
                t.status = S.REJECTED
                _log(t, u, f"{self.action}_rejected", d.get("note", ""))
            else:
                if self.side == "to" and d.get("to_classroom"):
                    t.to_classroom = _classroom(t.to_school, d["to_classroom"])
                t.status = self.approve_to
                _log(t, u, self.action, d.get("note", ""))
            t.save()
        audit(request, f"transfer.{self.action}", f"transfer:{t.pk}")
        return Response(_row(TransferRequest.objects.get(pk=t.pk)))


class ReleaseView(StepView):
    from_status, side, approve_to, action = S.PENDING_SOURCE, "from", S.PENDING_DEST, "released"


class AcceptView(StepView):
    from_status, side, approve_to, action = S.PENDING_DEST, "to", S.READY, "accepted"


class CompleteView(TransferView):
    """Executed by the destination school's admin once both sides agreed."""
    permission_classes = [IsAdmin]

    def post(self, request, pk):
        with transaction.atomic():
            t = get_object_or_404(TransferRequest.objects.select_for_update(of=("self",))
                                  .select_related("from_school", "to_school", "to_classroom"), pk=pk)
            if not in_scope(request.user, t.to_school):
                return Response(status=404)
            if t.status != S.READY:
                return Response({"detail": "درخواست هنوز تأیید نهایی نشده است."}, status=409)
            student = User.objects.select_for_update().get(pk=t.student_id)
            from apps.accounts.models import SchoolProfile, gender_allowed
            prof = SchoolProfile.objects.select_related("level").filter(school=t.to_school).first()
            if prof and prof.level.code in ("MS2", "KAR", "FNI") and not gender_allowed(student.gender, prof):
                return Response({"detail": "جنسیت دانش‌آموز با نوع مدرسه مقصد هم‌خوان نیست."}, status=409)
            if student.org_id != t.from_school_id:
                return Response({"detail": "مدرسه فعلی دانش‌آموز با درخواست یکی نیست؛ درخواست را لغو و دوباره ثبت کنید."}, status=409)
            if t.from_school_id == t.to_school_id and t.to_classroom_id is None:
                return Response({"detail": "کلاس مقصد انتخاب نشده است."}, status=400)
            execute(t, student, t.to_classroom)
            from .models import open_history
            open_history(student, t.to_school, reason=t.reason or "انتقال", transfer=t)
            t.save()
            _log(t, request.user, "completed", "")
        audit(request, "transfer.complete", f"transfer:{t.pk}")
        return Response(_row(TransferRequest.objects.get(pk=t.pk)))


class CancelView(TransferView):
    def post(self, request, pk):
        u = request.user
        with transaction.atomic():
            t = get_object_or_404(visible(u).select_for_update(of=("self",)), pk=pk)
            if t.status not in TransferRequest.ACTIVE:
                return Response({"detail": "این درخواست قابل لغو نیست."}, status=409)
            if not (t.requested_by_id == u.pk or in_scope(u, t.from_school) or in_scope(u, t.to_school)):
                raise PermissionDenied()
            t.status = S.CANCELLED
            t.save(update_fields=["status"])
            _log(t, u, "cancelled", "")
        return Response(_row(TransferRequest.objects.get(pk=t.pk)))


class StudentSearchView(TransferView):
    """GET /api/students/?q=  — admins find a student (by national id or name) inside their own scope."""
    permission_classes = [IsAdmin]

    def get(self, request):
        q = request.query_params.get("q", "").strip()
        if len(q) < 2:
            return Response([])
        u = request.user
        qs = User.objects.filter(role=User.Role.STUDENT, is_active=True, org__kind="school").select_related("org")
        if u.role != User.Role.SUPER_ADMIN:
            qs = qs.filter(scope_q(u, "org__path"))
        qs = qs.filter(Q(username__startswith=q) | Q(student_code__startswith=q) | Q(national_id__startswith=q)
                       | Q(last_name__icontains=q) | Q(first_name__icontains=q))
        return Response([{"id": s.pk, "username": s.username, "name": s.get_full_name(), "school": s.org.name}
                         for s in qs.order_by("last_name", "username")[:20]])


class SchoolSearchView(TransferView):
    """Destination schools may be outside the admin's own scope, so this is a name-only lookup."""
    permission_classes = [IsAdmin]

    def get(self, request):
        q = request.query_params.get("q", "").strip()
        if len(q) < 1:
            return Response([])
        rows = OrgUnit.objects.filter(kind="school", name__icontains=q).select_related("parent").order_by("name")[:20]
        return Response([{"id": s.pk, "name": s.name, "region": s.parent.name if s.parent_id else ""} for s in rows])
