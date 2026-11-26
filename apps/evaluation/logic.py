"""Academic evaluation from existing marks.

Course grade (نمره نهایی درس) =  (مستمر × ضریب مستمر + پایانی × ضریب پایانی) ÷ (ضریب مستمر + ضریب پایانی)
  * مستمر  = میانگین درصد نمره‌های مستمر: آزمون‌هایی با نوع «مستمر» و همه تکالیف تصحیح‌شده
  * پایانی = میانگین درصد آزمون‌های «پایانی»
  * اگر درس امتحان پایانی ندارد، نمره نهایی همان مستمر است.
  * اگر امتحان پایانی تعریف شده ولی هنوز نمره‌ای ندارد، نمره درس «در انتظار» است و در معدل نمی‌آید.
Overall average = mean of course grades. Pass = overall and every course at or above the policy pass mark.
"""
from collections import defaultdict

from django.db.models import Q

from apps.coursework.models import Submission
from apps.exams.models import Attempt, Exam

from .models import DEFAULT_BANDS, EvaluationPolicy


def _most_specific(org):
    if org is None:
        return None
    from apps.core.scope import ancestor_paths
    cands = EvaluationPolicy.objects.filter(Q(org__isnull=True) | Q(org__path__in=ancestor_paths(org.path)))
    best = None
    for p in cands:
        depth = 0 if p.org_id is None else len(p.org.path.strip("/").split("/"))
        if best is None or depth > best[0]:
            best = (depth, p)
    return best[1] if best else None


def policy_for(org):
    """(policy or None, pass mark, bands) that apply to `org`; the national default when none is set."""
    p = _most_specific(org)
    if p is None:
        return None, 50.0, DEFAULT_BANDS
    return p, float(p.pass_percent), (p.bands or DEFAULT_BANDS)


def weights_for(org):
    p = _most_specific(org)
    if p is None:
        return 1.0, 1.0
    return float(p.continuous_weight), float(p.final_weight)


def band_of(percent, bands):
    for b in sorted(bands, key=lambda b: -float(b["min"])):
        if percent >= float(b["min"]):
            return b["label"]
    return bands[-1]["label"]


def course_percentages(school_ids, student_ids=None, course_ids=None):
    """{(student_id, course_id): [course title, class name, continuous percents, final percents]}"""
    out = defaultdict(lambda: ["", "", [], []])
    att = Attempt.objects.filter(exam__course__org_id__in=school_ids, submitted_at__isnull=False, total__gt=0)
    sub = Submission.objects.filter(assignment__course__org_id__in=school_ids, points__isnull=False,
                                    assignment__max_points__gt=0)
    if student_ids is not None:
        att = att.filter(student_id__in=student_ids)
        sub = sub.filter(student_id__in=student_ids)
    if course_ids is not None:
        att = att.filter(exam__course_id__in=course_ids)
        sub = sub.filter(assignment__course_id__in=course_ids)
    for a in att.values("student_id", "exam__course_id", "exam__course__title", "exam__course__classroom__name",
                        "exam__assessment", "score", "total"):
        rec = out[(a["student_id"], a["exam__course_id"])]
        rec[0], rec[1] = a["exam__course__title"], a["exam__course__classroom__name"] or ""
        pct = float(a["score"]) / float(a["total"]) * 100
        (rec[3] if a["exam__assessment"] == "final" else rec[2]).append(pct)
    for s in sub.values("student_id", "assignment__course_id", "assignment__course__title",
                        "assignment__course__classroom__name", "points", "assignment__max_points"):
        rec = out[(s["student_id"], s["assignment__course_id"])]
        rec[0], rec[1] = s["assignment__course__title"], s["assignment__course__classroom__name"] or ""
        rec[2].append(float(s["points"]) / float(s["assignment__max_points"]) * 100)
    return out


def courses_with_final(school_ids):
    return set(Exam.objects.filter(course__org_id__in=school_ids, assessment="final")
               .values_list("course_id", flat=True))


def course_grade(cont, fin, has_final, wc, wf):
    """Return (grade or None when pending, continuous, final)."""
    c = sum(cont) / len(cont) if cont else None
    f = sum(fin) / len(fin) if fin else None
    if not has_final:
        return c, c, None
    if f is None:
        return None, c, None                           # final exam exists but has no marks yet
    if c is None:
        return f, None, f
    return (c * wc + f * wf) / (wc + wf), c, f


def evaluate_students(org, pct_map, pass_mark, bands, finals=None, weights=(1.0, 1.0)):
    """One row per student that has any marks, using the course-grade formula above."""
    wc, wf = weights
    finals = finals or set()
    per = defaultdict(list)
    for (sid, cid), (title, cls, cont, fin) in pct_map.items():
        grade, c, f = course_grade(cont, fin, cid in finals, wc, wf)
        if grade is None and c is None and f is None:
            continue
        per[sid].append({"course": title, "class": cls, "grade": grade, "continuous": c, "final": f,
                         "pending": grade is None})
    rows = []
    for sid, courses in per.items():
        graded = [c for c in courses if not c["pending"]]
        if not graded:
            continue
        overall = sum(c["grade"] for c in graded) / len(graded)
        failed = [c["course"] for c in graded if c["grade"] < pass_mark]
        rows.append({"student_id": sid, "courses": len(graded), "pending_courses": len(courses) - len(graded),
                     "failed_courses": len(failed), "failed_list": "، ".join(failed), "average": round(overall, 1),
                     "status": "قبول" if overall >= pass_mark and not failed else "مردود",
                     "band": band_of(overall, bands),
                     "course_detail": [{"course": c["course"], "class": c["class"],
                                        "continuous": None if c["continuous"] is None else round(c["continuous"], 1),
                                        "final": None if c["final"] is None else round(c["final"], 1),
                                        "average": None if c["grade"] is None else round(c["grade"], 1),
                                        "passed": None if c["grade"] is None else c["grade"] >= pass_mark,
                                        "pending": c["pending"]} for c in courses]})
    return rows
