from rest_framework.views import APIView
from decimal import Decimal
"""Question bank: reusable questions owned by an org unit; visible to that unit and everything below it."""
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts.models import OrgUnit, User
from apps.core.models import audit

from .authoring import AuthoringView, QuestionIn, staff_exams
from .models import BankChoice, BankQuestion, Choice, Question


def ancestor_paths(path):
    ids = [i for i in path.strip("/").split("/") if i]
    return ["/" + "/".join(ids[:n]) + "/" for n in range(1, len(ids) + 1)]


def visible_bank(user):
    """دسترسی بانک سؤال: خود معلم (پیش‌فرض)؛ مدرسه، منطقه یا استانی که سؤال را منتشر کرده؛ مدیر مدرسه همه‌ی سؤال‌های مدرسه‌اش."""
    qs = BankQuestion.objects.filter(is_active=True)
    if user.role == User.Role.SUPER_ADMIN:
        return qs
    q = Q(created_by=user)
    school = getattr(user, "org", None)
    if school is not None and school.path:
        my_path = school.path
        q |= Q(scope="school", org__path=my_path)
        # a region or province share is seen by every school below it
        q |= Q(scope__in=["region", "province"], share_org__path__in=ancestor_paths(my_path))
        if user.role == User.Role.SCHOOL_ADMIN:
            q |= Q(org__path=my_path)
    return qs.filter(q).distinct()


class BankInput(QuestionIn):
    chapter = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
    difficulty = serializers.ChoiceField(choices=["easy", "medium", "hard"], required=False, default="medium")
    scope = serializers.ChoiceField(choices=["private", "school", "region", "province"], required=False, default="private")
    share_org = serializers.PrimaryKeyRelatedField(queryset=OrgUnit.objects.all(), required=False, allow_null=True)
    org = serializers.PrimaryKeyRelatedField(queryset=OrgUnit.objects.all(), required=False)
    subject = serializers.IntegerField(required=False, allow_null=True)
    grade = serializers.IntegerField(required=False, allow_null=True)


def _row(b):
    return {"id": b.pk, "kind": b.kind, "text": b.text, "points": float(b.points), "org": b.org_id,
            "chapter": b.chapter, "difficulty": b.difficulty, "scope": b.scope, "share_org": b.share_org_id,
            "mine": b.created_by_id is not None,
            "subject": b.subject_id, "grade": b.grade_id, "choices": [
                {"text": c.text, "is_correct": c.is_correct} for c in b.choices.all()]}


class BankListCreateView(AuthoringView):
    def get(self, request):
        qs = visible_bank(request.user).prefetch_related("choices").order_by("-id")
        p = request.query_params
        for key in ("subject", "grade"):
            if p.get(key, "").isdigit():
                qs = qs.filter(**{f"{key}_id": int(p[key])})
        if p.get("kind") in ("mcq", "tf", "essay"):
            qs = qs.filter(kind=p["kind"])
        if p.get("q"):
            qs = qs.filter(text__icontains=p["q"][:100])
        if p.get("difficulty") in ("easy", "medium", "hard"):
            qs = qs.filter(difficulty=p["difficulty"])
        if p.get("chapter"):
            qs = qs.filter(chapter__icontains=p["chapter"][:100])
        if p.get("mine") == "1":
            qs = qs.filter(created_by=request.user)
        return Response([dict(_row(b), editable=_can_edit_bank(request.user, b)) for b in qs[:100]])

    def post(self, request):
        s = BankInput(data=request.data)
        s.is_valid(raise_exception=True)
        d, u = s.validated_data, request.user
        org = d.get("org") or u.org
        if org is None:
            raise PermissionDenied()
        from apps.core.scope import org_in_scope
        if not org_in_scope(u, org):
            raise PermissionDenied()                                   # may only own banks inside own scope
        with transaction.atomic():
            b = BankQuestion.objects.create(org=org, kind=d["kind"], text=d["text"], points=d["points"],
                                            subject_id=d.get("subject"), grade_id=d.get("grade"), created_by=u,
                                            chapter=d.get("chapter", ""), difficulty=d.get("difficulty", "medium"),
                                            scope=d.get("scope", "private"), share_org=d.get("share_org"))
            BankChoice.objects.bulk_create([BankChoice(question=b, text=c["text"], is_correct=c["is_correct"])
                                            for c in d["choices"]])
        audit(request, "bank.create", f"bank:{b.pk}")
        return Response({"id": b.pk}, status=201)


class AddFromBankInput(serializers.Serializer):
    bank_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1, max_length=100)


class AddFromBankView(AuthoringView):
    """POST /api/exams/<id>/add-from-bank/ — copies questions (so later bank edits never alter past exams)."""

    def post(self, request, pk):
        s = AddFromBankInput(data=request.data)
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            exam = get_object_or_404(staff_exams(request.user).select_for_update(of=("self",)), pk=pk)
            if exam.attempts.exists():
                return Response({"detail": "پس از شروع آزمون، سؤال‌ها قابل تغییر نیستند."}, status=409)
            banks = list(visible_bank(request.user).filter(pk__in=s.validated_data["bank_ids"])
                         .prefetch_related("choices"))
            if len(banks) != len(set(s.validated_data["bank_ids"])):
                return Response({"detail": "برخی سؤال‌های بانک در دسترس نیستند."}, status=400)
            if exam.questions.count() + len(banks) > 200:
                return Response({"detail": "حداکثر ۲۰۰ سؤال در هر آزمون."}, status=400)
            start = exam.questions.count()
            for i, b in enumerate(banks):
                q = Question.objects.create(exam=exam, kind=b.kind, text=b.text, points=b.points,
                                            order=start + i, bank_source=b)
                Choice.objects.bulk_create([Choice(question=q, text=c.text, is_correct=c.is_correct)
                                            for c in b.choices.all()])
            exam.save(update_fields=["updated_at"])
        audit(request, "exam.add_from_bank", f"exam:{exam.pk} n={len(banks)}")
        return Response({"added": len(banks)})


def _can_edit_bank(user, b):
    """ویرایش و حذف سؤال بانک: سازنده‌ی سؤال، مدیر مدرسه‌ی همان مدرسه، و مدیر سیستم."""
    if user.role == User.Role.SUPER_ADMIN or b.created_by_id == user.pk:
        return True
    school = getattr(user, "org", None)
    return user.role == User.Role.SCHOOL_ADMIN and school is not None and b.org.path == school.path


class BankDetailView(APIView):
    """PATCH /api/bank/<pk>/ — ویرایش متن، سرفصل، سختی، سطح دسترسی، واحد اشتراک و بارم.
    DELETE /api/bank/<pk>/ — حذف از بانک (سؤال‌های آزمون‌هایی که از آن کپی شده‌اند دست‌نخورده می‌مانند)."""

    def _get(self, request, pk):
        b = get_object_or_404(BankQuestion.objects.select_related("org"), pk=pk)
        if not visible_bank(request.user).filter(pk=b.pk).exists() or not _can_edit_bank(request.user, b):
            raise PermissionDenied("این سؤال را فقط سازنده یا مدیر مدرسه ویرایش و حذف می‌کند.")
        return b

    def patch(self, request, pk):
        b = self._get(request, pk)
        data = request.data
        if "text" in data:
            text = str(data.get("text") or "").strip()
            if not text:
                raise serializers.ValidationError({"text": "متن سؤال خالی است."})
            b.text = text[:20000]
        if "chapter" in data:
            b.chapter = str(data.get("chapter") or "")[:200]
        if "difficulty" in data:
            if data["difficulty"] not in ("easy", "medium", "hard"):
                raise serializers.ValidationError({"difficulty": "سختی درست نیست."})
            b.difficulty = data["difficulty"]
        if "scope" in data:
            if data["scope"] not in ("private", "school", "region", "province"):
                raise serializers.ValidationError({"scope": "سطح دسترسی درست نیست."})
            b.scope = data["scope"]
        if "share_org" in data:
            b.share_org = OrgUnit.objects.filter(pk=data["share_org"]).first() if data["share_org"] else None
        if b.scope in ("region", "province") and b.share_org is None:
            raise serializers.ValidationError({"share_org": "برای منطقه یا استان، واحد اشتراک را انتخاب کنید."})
        if "points" in data:
            try:
                p = Decimal(str(data["points"]))
            except Exception:
                raise serializers.ValidationError({"points": "بارم باید عدد باشد."})
            if p <= 0 or p > 100:
                raise serializers.ValidationError({"points": "بارم بین ۰ تا ۱۰۰ باشد."})
            b.points = p
        b.save()
        audit(request, "exam.bank.edit", f"bank:{b.pk}")
        return Response(_row(b))

    def delete(self, request, pk):
        b = self._get(request, pk)
        audit(request, "exam.bank.delete", f"bank:{b.pk}")
        b.delete()
        return Response(status=204)
