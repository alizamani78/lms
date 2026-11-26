import random
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Answer, Attempt, Question, QuestionKind

GRACE_SECONDS = 5


def draw_questions(exam, all_ids, student_id):
    """Deterministic per (exam, student): the same student always gets the same subset and order."""
    ids = list(all_ids)
    rng = random.Random(f"{exam.pk}:{student_id}")
    if exam.draw_count and exam.draw_count < len(ids):
        ids = rng.sample(ids, exam.draw_count)
    if exam.shuffle_questions:
        rng.shuffle(ids)
    return ids


def _percent_sum(weights):
    return sum((Decimal(w) for w in weights), Decimal(0))


def _multi_award(points, options, chosen):
    """Each correct option ticked adds its number of points; each wrong option ticked takes its points away.
    The result stays between 0 and the question's points."""
    gain = sum((Decimal(str(o["weight"])) for o in options if o["id"] in chosen and o["is_correct"]), Decimal(0))
    loss = sum((Decimal(str(o["weight"])) for o in options if o["id"] in chosen and not o["is_correct"]), Decimal(0))
    return min(points, max(Decimal(0), gain - loss))


def _pair_award(points, options, mapping):
    """Match / drag: each correctly placed item earns its percentage of the question's points."""
    share = _percent_sum(o["weight"] for o in options if o["pair_text"].strip()
                         and str(mapping.get(str(o["id"]), "")).strip() == o["pair_text"].strip()) / Decimal(100)
    return min(points, max(Decimal(0), points * share))


def question_awards(attempt):
    """{question id: awarded points} for the attempt (essays only once the teacher has graded them), and whether
    an essay is still waiting for the teacher."""
    from .models import Choice
    ids = attempt.question_ids or list(Question.objects.filter(exam_id=attempt.exam_id).values_list("id", flat=True))
    questions = {q["id"]: q for q in Question.objects.filter(pk__in=ids).values("id", "kind", "points")}
    options = {}
    for c in Choice.objects.filter(question_id__in=list(questions)).values("id", "question_id", "is_correct", "text", "pair_text", "weight"):
        options.setdefault(c["question_id"], []).append(c)
    awards, pending = {qid: Decimal(0) for qid in questions}, False
    answers = Answer.objects.filter(attempt_id=attempt.pk, question_id__in=list(questions)).values(
        "question_id", "choice_id", "choice__is_correct", "choice_ids", "mapping", "points_awarded", "text")
    for a in answers:
        q = questions[a["question_id"]]
        opts = options.get(a["question_id"], [])
        kind = q["kind"]
        if kind == QuestionKind.ESSAY:
            if a["points_awarded"] is not None:
                awards[q["id"]] = Decimal(a["points_awarded"])
            elif a["text"].strip():
                pending = True
        elif kind == QuestionKind.MULTI:
            chosen = {int(x) for x in a["choice_ids"] or []}
            awards[q["id"]] = _multi_award(q["points"], opts, chosen)
        elif kind == QuestionKind.SHORT:
            from apps.core.persian import canonical
            accepted = {canonical(o["text"]) for o in opts if o["is_correct"]}
            if accepted and canonical(a["text"] or "") in accepted:
                awards[q["id"]] = Decimal(q["points"])
        elif kind == QuestionKind.FILL:
            import json
            from apps.core.persian import canonical
            try:
                given = json.loads(a["text"] or "{}")
            except ValueError:
                given = {}
            if not isinstance(given, dict):
                given = {}
            total = Decimal(0)
            for o in opts:
                accepted = {canonical(x) for x in (o["text"] or "").split("|") if x.strip()}
                answer = canonical(str(given.get(str(o["pair_text"] or "").strip(), "")))
                if accepted and answer in accepted:
                    total += Decimal(q["points"]) * Decimal(str(o["weight"] or 0)) / Decimal(100)
            awards[q["id"]] = total.quantize(Decimal("0.01"))
        elif kind in (QuestionKind.MATCH, QuestionKind.DRAG):
            awards[q["id"]] = _pair_award(q["points"], opts, a["mapping"] or {})
        elif a["choice__is_correct"]:
            awards[q["id"]] = Decimal(q["points"])
    return awards, pending, questions


def compute_scores(attempt):
    """Return (score, total, pending_essays). Essays count only after a teacher has graded them."""
    awards, pending, questions = question_awards(attempt)
    total = sum((q["points"] for q in questions.values()), Decimal(0))
    score = sum(awards.values(), Decimal(0)) + (attempt.adjustment or Decimal(0))
    penalty = Decimal(attempt.back_moves or 0) * (attempt.exam.back_penalty or Decimal(0))
    score = max(Decimal(0), score - penalty)
    return score, total, pending


def result_for_student(attempt):
    """What the student may see after the submit, as the exam's designer chose (hidden / score / counts / both)."""
    exam = attempt.exam
    mode = exam.result_mode if exam.result_mode != "hidden" else ("score" if exam.show_results else "hidden")
    if mode == "hidden":
        return {"submitted": True}
    awards, pending, questions = question_awards(attempt)
    score, total, _ = compute_scores(attempt)
    out = {"submitted": True, "pending_review": pending}
    if mode in ("score", "both"):
        out.update(score=float(score), total=float(total))
    if mode in ("counts", "both"):
        correct = sum(1 for qid, q in questions.items() if awards.get(qid, 0) >= q["points"] and q["points"] > 0)
        wrong = sum(1 for qid, q in questions.items() if awards.get(qid, 0) <= 0)
        out.update(correct=correct, wrong=wrong, partial=len(questions) - correct - wrong, questions=len(questions))
    return out


def grade_and_close(attempt):
    """Idempotent: caller must hold a row lock on `attempt` (select_for_update)."""
    if attempt.submitted_at is not None:
        return attempt
    attempt.score, attempt.total, pending = compute_scores(attempt)
    attempt.grading_status = Attempt.Grading.PENDING if pending else Attempt.Grading.GRADED
    attempt.submitted_at = timezone.now()
    attempt.save(update_fields=["score", "total", "grading_status", "submitted_at"])
    return attempt


def submit_attempt(attempt_id, student_id=None):
    with transaction.atomic():
        qs = Attempt.objects.select_for_update().select_related("exam")
        if student_id is not None:
            qs = qs.filter(student_id=student_id)
        return grade_and_close(qs.get(pk=attempt_id))
