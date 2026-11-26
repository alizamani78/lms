from decimal import Decimal
"""Teacher/admin exam authoring. Students never reach these views (role + scope checks)."""
from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_scope_q
from apps.courses.models import Course

from .models import ExamSection, Choice, Exam, Question, QuestionKind

STAFF = role_required(*User.STAFF_ROLES)
MAX_QUESTIONS = 200


def staff_courses(user):
    qs = Course.objects.all()
    if user.role == User.Role.TEACHER:
        return qs.filter(teacher=user)
    return qs.filter(org_scope_q(user, "org"))


def staff_courses_in(request):
    from apps.core.scope import kartabl_filter
    return staff_courses(request.user).filter(**kartabl_filter(request, "org_id"))


def staff_exams(user):
    qs = Exam.objects.all()
    if user.role == User.Role.TEACHER:
        return qs.filter(course__teacher=user)
    return qs.filter(org_scope_q(user, "course__org"))


class ChoiceIn(serializers.Serializer):
    text = serializers.CharField(max_length=4000, trim_whitespace=False)
    pair_text = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    is_correct = serializers.BooleanField(default=False)
    weight = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, required=False, default=0)


def _share_weights(choices, counts):
    """Percentages of the question's points. Options that gain (counts) must add up to at most 100%; when no percentage
    was entered, the gaining options share 100% equally. Penalty options keep the percentage given (0 = no penalty)."""
    gaining = [c for c in choices if counts(c)]
    if not gaining:
        return choices
    if all(Decimal(str(c.get("weight") or 0)) == 0 for c in gaining):
        share = (Decimal(100) / Decimal(len(gaining))).quantize(Decimal("0.01"))
        for c in gaining:
            c["weight"] = share
    total = _sum_weights(gaining)
    if total > 100:
        raise serializers.ValidationError("جمع درصدهای نمره پاسخ‌های صحیح نباید بیشتر از ۱۰۰٪ بارم سؤال باشد.")
    return choices


def _sum_weights(items):
    return sum((Decimal(str(c.get("weight") or 0)) for c in items), Decimal(0))


class QuestionIn(serializers.Serializer):
    """Question kinds and their rules (the teacher's editor shows the matching fields):
      mcq   2-6 options, exactly one correct          tf      tf_answer
      multi 2-8 options, at least one correct          short   1-5 accepted answers (is_correct)
      match 2-8 pairs: text = left, pair_text = right  drag    2-8 items: pair_text = the slot each belongs to
      essay no options (graded by the teacher)"""
    kind = serializers.ChoiceField(choices=[c[0] for c in QuestionKind.choices], default="mcq")
    text = serializers.CharField(max_length=20000, trim_whitespace=False)
    points = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, default=1)
    difficulty = serializers.ChoiceField(choices=["easy", "medium", "hard"], default="medium", required=False)
    section = serializers.IntegerField(required=False, allow_null=True, min_value=0)     # index in the exam's sections
    choices = ChoiceIn(many=True, required=False, max_length=8)
    tf_answer = serializers.BooleanField(required=False)

    def validate(self, data):
        from apps.core.htmlclean import clean_html
        kind, choices = data["kind"], data.get("choices") or []
        data["text"] = clean_html(data["text"])[0]
        for c in choices:
            c["text"] = clean_html(c["text"])[0] if kind in ("mcq", "multi", "match", "drag") else c["text"].strip()
            c["pair_text"] = (c.get("pair_text") or "").strip()
        if kind == "mcq":
            if len(choices) < 2:
                raise serializers.ValidationError("سؤال تک‌گزینه‌ای باید حداقل ۲ گزینه داشته باشد.")
            correct = sum(1 for c in choices if c["is_correct"])
            if correct == 0:
                raise serializers.ValidationError("هیچ پاسخ صحیحی مشخص نشده است؛ گزینه‌ی درست را علامت بزنید (فقط یک گزینه).")
            if correct > 1:
                raise serializers.ValidationError("سؤال تک‌گزینه‌ای فقط یک پاسخ صحیح دارد؛ یکی از علامت‌ها را بردارید.")
        elif kind == "tf":
            if "tf_answer" not in data:
                raise serializers.ValidationError("برای سؤال صحیح/غلط، tf_answer لازم است.")
            choices = [{"text": "درست", "is_correct": bool(data["tf_answer"])},
                       {"text": "نادرست", "is_correct": not data["tf_answer"]}]
        elif kind == "multi":
            if len(choices) < 2:
                raise serializers.ValidationError("سؤال چندگزینه‌ای باید حداقل ۲ گزینه داشته باشد.")
            if not any(c["is_correct"] for c in choices):
                raise serializers.ValidationError("هیچ پاسخ صحیحی مشخص نشده است؛ حداقل یک گزینه‌ی درست را علامت بزنید.")
            pts = Decimal(str(data.get("points") or 0))
            for c in choices:
                c["weight"] = Decimal(str(c.get("weight") or 0))
                if c["is_correct"] and c["weight"] <= 0:
                    raise serializers.ValidationError("هر گزینه‌ی صحیح چندپاسخ باید نمره‌ی عددی بزرگ‌تر از صفر داشته باشد.")
            gains = sum((c["weight"] for c in choices if c["is_correct"]), Decimal(0))
            if gains > pts:
                raise serializers.ValidationError(f"مجموع نمره‌ی گزینه‌های صحیح ({gains}) نباید از بارم سؤال ({pts}) بیشتر باشد.")
        elif kind == "short":
            accepted = [c for c in choices if c["is_correct"] and c["text"]]
            if not accepted:
                raise serializers.ValidationError("برای پاسخ کوتاه، حداقل یک پاسخ صحیح (پاسخ قابل قبول) لازم است.")
            choices = [{**c, "is_correct": True} for c in accepted[:5]]
        elif kind == "fill":
            text = data.get("text") or ""
            if not choices:
                raise serializers.ValidationError({"choices": "برای سؤال تکمیل جای خالی، حداقل یک جای خالی با پاسخ قابل قبول تعریف کنید."})
            numbers = [str(c.get("pair_text") or "").strip() for c in choices]
            if any(not n.isdigit() for n in numbers) or len(set(numbers)) != len(numbers):
                raise serializers.ValidationError({"choices": "شماره‌ی هر جای خالی باید عدد و یکتا باشد (مثلاً ۱، ۲)."})
            missing = [n for n in numbers if "{" + n + "}" not in text]
            if missing:
                n = missing[0]
                raise serializers.ValidationError({"text": (
                    "متن سؤال جای خالی شماره‌ی " + n + " را ندارد. محل آن را در متن با عبارت «{" + n + "}» مشخص کنید؛ "
                    "مثلاً: «پایتخت ایران {" + n + "} است.»")})
            shares = []
            for c in choices:
                if not (c.get("text") or "").strip():
                    raise serializers.ValidationError({"choices": "پاسخ قابل قبول هر جای خالی را وارد کنید (چند پاسخ را با | جدا کنید)."})
                w = Decimal(str(c.get("weight") or 0))
                if w <= 0:
                    raise serializers.ValidationError({"choices": "درصد نمره‌ی هر جای خالی باید بیشتر از صفر باشد."})
                shares.append(w)
            if sum(shares) > 100:
                raise serializers.ValidationError({"choices": "مجموع درصد جای خالی‌ها " + str(sum(shares)) + "٪ است و نباید از ۱۰۰٪ بیشتر باشد."})
            for c in choices:
                c["is_correct"] = True
                c["weight"] = Decimal(str(c.get("weight") or 0))
        elif kind in ("match", "drag"):
            if len(choices) < 2 or any(not c["pair_text"] for c in choices):
                raise serializers.ValidationError(
                    "برای جور کردنی و درگ اند دراپ، حداقل ۲ مورد لازم است و هر مورد باید جای درست (سمت مقابل) داشته باشد.")
            for c in choices:
                c["is_correct"] = True
            choices = _share_weights(choices, lambda c: True)
        else:
            choices = []
        data["choices"] = choices
        return data


class QuestionsBody(serializers.Serializer):
    questions = QuestionIn(many=True, max_length=MAX_QUESTIONS)


class SectionIn(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    instructions = serializers.CharField(max_length=20000, required=False, allow_blank=True, default="")


class ExamIn(serializers.ModelSerializer):
    questions = QuestionIn(many=True, required=False, max_length=MAX_QUESTIONS)
    sections = SectionIn(many=True, required=False, max_length=50)

    class Meta:
        model = Exam
        fields = ("title", "instructions", "result_mode", "starts_at", "ends_at", "duration_minutes", "show_results", "assessment",
                  "shuffle_questions", "shuffle_choices", "draw_count", "is_suspended", "questions", "sections",
                  "allow_back", "back_penalty", "questions_per_screen")

    def validate(self, data):
        start = data.get("starts_at", getattr(self.instance, "starts_at", None))
        end = data.get("ends_at", getattr(self.instance, "ends_at", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"ends_at": "پایان آزمون باید بعد از شروع آن باشد."})
        return data

    def validate_duration_minutes(self, v):
        if not 1 <= v <= 600:
            raise serializers.ValidationError("مدت آزمون باید بین ۱ تا ۶۰۰ دقیقه باشد.")
        return v

    def validate(self, attrs):
        starts = attrs.get("starts_at", getattr(self.instance, "starts_at", None))
        ends = attrs.get("ends_at", getattr(self.instance, "ends_at", None))
        if starts and ends and ends <= starts:
            raise serializers.ValidationError({"ends_at": "پایان باید بعد از شروع باشد."})
        return attrs


def write_sections(exam, sections):
    """Replace the sections of `exam`; returns the new sections in the order given (position = index)."""
    exam.sections.all().delete()
    return [ExamSection.objects.create(exam=exam, title=s["title"], instructions=s.get("instructions", ""), order=i)
            for i, s in enumerate(sections or [])]


def write_questions(exam, questions, sections=None):
    """Replace all questions of `exam` (caller handles locking and the no-attempts rule)."""
    exam.questions.all().delete()
    made = write_sections(exam, sections) if sections is not None else list(exam.sections.all())
    def _sec(q):
        i = q.get("section")
        return made[i] if i is not None and 0 <= i < len(made) else None
    qs = Question.objects.bulk_create(
        [Question(exam=exam, kind=q["kind"], text=q["text"], points=q["points"], order=i, difficulty=q.get("difficulty") or "medium",
                  section=_sec(q))
         for i, q in enumerate(questions)])
    Choice.objects.bulk_create(
        [Choice(question=q, text=c["text"], pair_text=c.get("pair_text", ""), is_correct=c["is_correct"],
               weight=Decimal(str(c.get("weight") or 0)))
         for q, data in zip(qs, questions) for c in data["choices"]])


def exam_detail(exam):
    exam = Exam.objects.prefetch_related(
        Prefetch("questions", queryset=Question.objects.prefetch_related("choices"))).get(pk=exam.pk)
    return {
        "id": exam.pk, "course": exam.course_id, "school": exam.course.org_id, "title": exam.title, "starts_at": exam.starts_at,
        "ends_at": exam.ends_at, "duration_minutes": exam.duration_minutes,
        "is_published": exam.is_published, "is_suspended": exam.is_suspended, "show_results": exam.show_results,
        "instructions": exam.instructions, "result_mode": exam.result_mode,
        "shuffle_questions": exam.shuffle_questions, "shuffle_choices": exam.shuffle_choices, "assessment": exam.assessment,
        "draw_count": exam.draw_count, "allow_back": exam.allow_back, "back_penalty": float(exam.back_penalty),
        "questions_per_screen": exam.questions_per_screen,
        "sections": [{"id": x.pk, "title": x.title, "instructions": x.instructions, "order": x.order} for x in exam.sections.all()],
        "questions": [{"id": q.pk, "kind": q.kind, "difficulty": q.difficulty, "section": q.section_id, "points": float(q.points), "text": q.text,
                       "choices": [{"id": c.pk, "text": c.text, "is_correct": c.is_correct, "pair_text": c.pair_text, "weight": float(c.weight)}
                                   for c in q.choices.all()]} for q in exam.questions.all()],
    }


class AuthoringView(APIView):
    permission_classes = [STAFF]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


class ExamCreateView(AuthoringView):
    def post(self, request, course_id):
        course = get_object_or_404(staff_courses(request.user), pk=course_id)
        s = ExamIn(data=request.data)
        s.is_valid(raise_exception=True)
        data = dict(s.validated_data)
        questions = data.pop("questions", [])
        sections = data.pop("sections", None)
        with transaction.atomic():
            exam = Exam.objects.create(course=course, **data)
            write_questions(exam, questions, sections)
        audit(request, "exam.create", f"exam:{exam.pk}")
        return Response({"id": exam.pk}, status=201)


class ExamManageView(AuthoringView):
    def get(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)
        return Response(exam_detail(exam))

    def patch(self, request, pk):
        with transaction.atomic():
            exam = get_object_or_404(staff_exams(request.user).select_for_update(of=("self",)), pk=pk)
            s = ExamIn(exam, data=request.data, partial=True)
            s.is_valid(raise_exception=True)
            data = dict(s.validated_data)
            if data.pop("questions", None) is not None:
                return Response({"detail": "برای تغییر سؤال‌ها از /questions/ استفاده کنید."}, status=400)
            timing = {"starts_at", "ends_at", "duration_minutes"} & set(data)
            if timing and exam.attempts.exists():
                return Response({"detail": "پس از شروع آزمون، زمان‌بندی قابل تغییر نیست."}, status=409)
            for k, v in data.items():
                setattr(exam, k, v)
            exam.save()
        audit(request, "exam.update", f"exam:{exam.pk}")
        return Response(exam_detail(exam))


class QuestionsReplaceView(AuthoringView):
    def put(self, request, pk):
        s = QuestionsBody(data=request.data)
        s.is_valid(raise_exception=True)
        with transaction.atomic():
            exam = get_object_or_404(staff_exams(request.user).select_for_update(of=("self",)), pk=pk)
            if exam.attempts.exists():
                return Response({"detail": "پس از شروع آزمون، سؤال‌ها قابل تغییر نیستند."}, status=409)
            write_questions(exam, s.validated_data["questions"], s.validated_data.get("sections"))
            exam.save(update_fields=["updated_at"])      # new cache key for the question payload
        audit(request, "exam.questions", f"exam:{exam.pk}")
        return Response(exam_detail(exam))


class PublishInput(serializers.Serializer):
    published = serializers.BooleanField()


class PublishView(AuthoringView):
    def post(self, request, pk):
        s = PublishInput(data=request.data)
        s.is_valid(raise_exception=True)
        publish = s.validated_data["published"]
        with transaction.atomic():
            exam = get_object_or_404(staff_exams(request.user).select_for_update(of=("self",)), pk=pk)
            n = exam.questions.count()
            if publish and n == 0:
                return Response({"detail": "آزمون بدون سؤال قابل انتشار نیست."}, status=400)
            if publish and exam.draw_count and exam.draw_count > n:
                return Response({"detail": "تعداد سؤال تصادفی از تعداد سؤال‌های آزمون بیشتر است."}, status=400)
            exam.is_published = publish
            exam.save(update_fields=["is_published", "updated_at"])
        audit(request, "exam.publish" if publish else "exam.unpublish", f"exam:{exam.pk}")
        return Response({"id": exam.pk, "is_published": exam.is_published})


class ExamSectionsView(AuthoringView):
    """PUT /api/exams/<id>/sections/ — save the exam's sections and which section each question belongs to.
    body: {sections: [{title, instructions}], assign: {"<question id>": <section index> | null}}.
    Questions are never removed here; a section that has questions can be renamed or moved, and deleting one
    leaves its questions outside any section."""

    def put(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user).select_for_update(of=("self",)), pk=pk)
        s = SectionsIn(data=request.data)
        s.is_valid(raise_exception=True)
        sections = s.validated_data["sections"]
        assign = s.validated_data.get("assign") or {}
        with transaction.atomic():
            made = write_sections(exam, [{"title": x["title"], "instructions": x.get("instructions", "")} for x in sections])
            for qid, idx in assign.items():
                q = exam.questions.filter(pk=int(qid)).first()
                if q is None:
                    continue
                q.section = made[int(idx)] if idx is not None and 0 <= int(idx) < len(made) else None
                q.save(update_fields=["section"])
        return Response(exam_detail(exam))


class SectionsIn(serializers.Serializer):
    sections = SectionIn(many=True, max_length=50)
    assign = serializers.DictField(child=serializers.IntegerField(allow_null=True), required=False, default=dict)
