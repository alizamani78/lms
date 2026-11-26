from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_scope_q

from .models import AppealEvent, GradeAppeal
from .services import apply_decision, eligible, resolve_item, window

S = GradeAppeal.Status
IsStudent = role_required(User.Role.STUDENT)


class AppealView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


def visible(user):
    qs = GradeAppeal.objects.select_related("course", "student")
    if user.role == User.Role.STUDENT:
        return qs.filter(student=user)
    if user.role == User.Role.TEACHER:
        return qs.filter(course__teacher=user)
    if user.role in User.ADMIN_ROLES:
        return qs.filter(org_scope_q(user, "course__org"))
    return qs.none()


def _log(appeal, actor, action, note=""):
    AppealEvent.objects.create(appeal=appeal, actor=actor, action=action, note=note[:500])


def _row(a, detail=False):
    row = {"id": a.pk, "kind": a.kind, "kind_label": a.get_kind_display(), "ref_id": a.ref_id, "status": a.status,
           "status_label": a.get_status_display(), "course": a.course.title, "text": a.text, "response": a.response,
           "old_points": None if a.old_points is None else float(a.old_points),
           "new_points": None if a.new_points is None else float(a.new_points), "created_at": a.created_at,
           "student": a.student.username, "student_name": a.student.get_full_name()}
    if detail:
        item = resolve_item(a.kind, a.ref_id, a.student)
        row["item"] = None if item is None else {"label": item["label"], "points": float(item["points"]),
                                                  "max": float(item["max"])}
        row["events"] = [{"action": e.action, "note": e.note, "at": e.at,
                          "actor": e.actor.get_full_name() or e.actor.username if e.actor else ""}
                         for e in a.events.select_related("actor")]
    return row


class AppealIn(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[c[0] for c in GradeAppeal.Kind.choices])
    ref_id = serializers.IntegerField()
    text = serializers.CharField(min_length=5, max_length=2000)


class AppealListCreateView(AppealView):
    def get(self, request):
        qs = visible(request.user)
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response([_row(a) for a in qs.order_by("-created_at")[:200]])

    def post(self, request):
        if request.user.role != User.Role.STUDENT:
            raise PermissionDenied()
        s = AppealIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        item = resolve_item(d["kind"], d["ref_id"], request.user)
        if item is None:
            return Response({"detail": "این مورد قابل اعتراض نیست."}, status=404)
        if item["graded_at"] is None or timezone.now() - item["graded_at"] > window():
            return Response({"detail": "مهلت اعتراض به این نمره گذشته است."}, status=403)
        try:
            with transaction.atomic():
                a = GradeAppeal.objects.create(student=request.user, course=item["course"], kind=d["kind"],
                                               ref_id=d["ref_id"], text=d["text"], old_points=item["points"])
                _log(a, request.user, "filed", d["text"])
        except IntegrityError:
            return Response({"detail": "برای این مورد قبلاً اعتراض ثبت شده است."}, status=409)
        audit(request, "appeal.file", f"appeal:{a.pk}")
        return Response({"id": a.pk}, status=201)


class EligibleView(AppealView):
    permission_classes = [IsStudent]

    def get(self, request):
        return Response(eligible(request.user))


class AppealDetailView(AppealView):
    def get(self, request, pk):
        return Response(_row(get_object_or_404(visible(request.user), pk=pk), detail=True))


class DecideIn(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["accept", "reject"])
    response = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    new_points = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=0, required=False)


class DecideView(AppealView):
    """Teacher decides first; after escalation only an admin (inside scope) decides."""

    def post(self, request, pk):
        u = request.user
        if u.role not in User.STAFF_ROLES:
            raise PermissionDenied()
        s = DecideIn(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        with transaction.atomic():
            a = get_object_or_404(visible(u).select_for_update(of=("self",)), pk=pk)
            if a.status == S.OPEN:
                pass
            elif a.status == S.ESCALATED:
                if u.role not in User.ADMIN_ROLES:
                    raise PermissionDenied("پس از ارجاع، فقط مدیر تصمیم می‌گیرد.")
            else:
                return Response({"detail": "این اعتراض در مرحله تصمیم‌گیری نیست."}, status=409)
            response = d.get("response", "").strip()
            if d["decision"] == "reject":
                if len(response) < 5:
                    raise serializers.ValidationError({"response": "دلیل رد اعتراض را بنویسید."})
                a.status = S.REJECTED if a.status == S.OPEN else S.REJECTED_FINAL
            else:
                if "new_points" not in d:
                    raise serializers.ValidationError({"new_points": "نمره جدید لازم است."})
                apply_decision(request, a, d["new_points"])
                a.status, a.new_points = S.ACCEPTED, d["new_points"]
            a.response, a.decided_by, a.decided_at = response, u, timezone.now()
            a.save(update_fields=["status", "new_points", "response", "decided_by", "decided_at"])
            _log(a, u, d["decision"], response)
        audit(request, f"appeal.{d['decision']}", f"appeal:{a.pk}")
        return Response(_row(a))


class StudentActionView(AppealView):
    permission_classes = [IsStudent]
    allowed_from = ()
    to_status = None
    action = ""

    def post(self, request, pk):
        with transaction.atomic():
            a = get_object_or_404(GradeAppeal.objects.select_for_update(of=("self",)).select_related("course", "student"),
                                  pk=pk, student=request.user)
            if a.status not in self.allowed_from:
                return Response({"detail": "این عملیات در وضعیت فعلی اعتراض ممکن نیست."}, status=409)
            a.status = self.to_status
            a.save(update_fields=["status"])
            _log(a, request.user, self.action, (request.data.get("note") or "")[:500] if hasattr(request.data, "get") else "")
        return Response(_row(a))


class EscalateView(StudentActionView):
    allowed_from = (S.REJECTED,)
    to_status = S.ESCALATED
    action = "escalated"


class WithdrawView(StudentActionView):
    allowed_from = (S.OPEN, S.REJECTED, S.ESCALATED)
    to_status = S.WITHDRAWN
    action = "withdrawn"
