"""گردش‌کار نمره‌ی امتحان: موقت ← اعتراض دانش‌آموز ← نظر و تغییر دبیر ← تأیید معاون/مدیر (نهایی، قفل).
- دانش‌آموز: نمره‌ی موقت خود را می‌بیند و یک بار اعتراض می‌کند (تا قبل از تأیید نهایی).
- دبیر درس: نمره را ثبت یا پس از اعتراض/بازکردن قفل تغییر می‌دهد و نظرش را می‌نویسد.
- معاون آموزشی / مدیر مدرسه / مدیر سیستم: تأیید نهایی؛ مدیر مدرسه (و مدیر سیستم) بازکردن قفل نمره‌ی نهایی."""
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit
from apps.core.scope import org_in_scope
from apps.evaluation.models import ClassGrade

from .models import Exam, ExamScoreStatus

APPROVERS = ("super_admin", "school_admin", "school_deputy_edu")
UNLOCKERS = ("super_admin", "school_admin")
EDITING = (ExamScoreStatus.TEMPORARY, ExamScoreStatus.APPEALED, ExamScoreStatus.UNLOCKED)


def _status(exam, student_id):
    st, _ = ExamScoreStatus.objects.get_or_create(exam=exam, student_id=student_id)
    return st


def _grade(exam, student_id):
    return ClassGrade.objects.filter(course=exam.course, student_id=student_id, title=exam.title, period="final").first()


def is_locked(exam, student_id):
    """نمره‌ی نهایی قفل است؛ هیچ‌کس جز مدیر (با بازکردن قفل) آن را تغییر نمی‌دهد."""
    return ExamScoreStatus.objects.filter(exam=exam, student_id=student_id, status=ExamScoreStatus.FINAL).exists()


class ExamScoreReviewView(APIView):
    """GET /api/exams/<pk>/score-review/ — فهرست نمره‌ها و وضعیت‌ها (دبیر و مدیران).
    POST {action, student, ...}:
      review  (دبیر): {score, note} — تغییر نمره و نظر پس از اعتراض یا بازکردن قفل
      approve (معاون/مدیر): تأیید نهایی و قفل
      unlock  (مدیر مدرسه/سیستم): {reason} — بازکردن قفل نمره‌ی نهایی برای اصلاح دبیر
      appeal  (دانش‌آموز): {text} — اعتراض یک‌بار، تا قبل از تأیید نهایی"""

    def _exam(self, request, pk):
        exam = get_object_or_404(Exam.objects.select_related("course__org", "course__teacher"), pk=pk)
        return exam

    def _is_teacher(self, user, exam):
        return exam.course.teacher_id == user.pk

    def _manager(self, user, exam):
        return user.role == "super_admin" or (user.role in APPROVERS and org_in_scope(user, exam.course.org))

    def get(self, request, pk):
        exam = self._exam(request, pk)
        if not (self._is_teacher(request.user, exam) or self._manager(request.user, exam)):
            raise PermissionDenied("فهرست نمره‌ها را دبیر درس یا مدیران مدرسه می‌بینند.")
        statuses = {s.student_id: s for s in ExamScoreStatus.objects.filter(exam=exam).select_related("student")}
        grades = {g.student_id: g for g in ClassGrade.objects.filter(course=exam.course, title=exam.title, period="final")}
        rows = []
        for sid, g in grades.items():
            st = statuses.get(sid)
            stu = User.objects.get(pk=sid)
            rows.append({"student": sid, "name": stu.get_full_name() or stu.username, "code": stu.student_code or "",
                         "class": "", "score": float(g.score), "status": st.status if st else ExamScoreStatus.TEMPORARY,
                         "status_label": dict(ExamScoreStatus.STATUS_CHOICES).get(st.status if st else "temporary", ""),
                         "appeal": st.appeal_text if st else "", "note": st.teacher_note if st else ""})
        return Response({"exam": exam.title, "rows": rows, "can_teacher_edit": self._is_teacher(request.user, exam),
                         "can_approve": self._manager(request.user, exam), "can_unlock": request.user.role in UNLOCKERS or request.user.role == "super_admin"})

    def post(self, request, pk):
        exam = self._exam(request, pk)
        action = request.data.get("action")
        user = request.user
        sid = int(request.data.get("student") or 0)
        if action == "appeal":
            if user.pk != sid:
                raise PermissionDenied("اعتراض را فقط خود دانش‌آموز ثبت می‌کند.")
            st = _status(exam, sid)
            if st.status == ExamScoreStatus.FINAL:
                raise serializers.ValidationError({"status": "نمره‌ی نهایی است و اعتراض ممکن نیست."})
            if st.appeal_at is not None:
                raise serializers.ValidationError({"status": "هر دانش‌آموز فقط یک بار حق اعتراض دارد."})
            text = str(request.data.get("text") or "").strip()[:1000]
            if not text:
                raise serializers.ValidationError({"text": "دلیل اعتراض را بنویسید."})
            from django.utils import timezone
            st.status, st.appeal_text, st.appeal_at = ExamScoreStatus.APPEALED, text, timezone.now()
            st.save()
            audit(request, "exam.score.appeal", f"exam:{exam.pk} student:{sid}")
            return Response({"ok": True, "status": st.status})

        if action == "review":
            if not self._is_teacher(user, exam):
                raise PermissionDenied("نمره را فقط دبیر همان درس تغییر می‌دهد.")
            st = _status(exam, sid)
            if st.status not in EDITING:
                raise serializers.ValidationError({"status": "این نمره قفل است؛ مدیر مدرسه باید قفل را باز کند."})
            g = _grade(exam, sid)
            if g is None:
                raise serializers.ValidationError({"student": "برای این دانش‌آموز نمره‌ای ثبت نشده است."})
            if request.data.get("score") not in (None, ""):
                try:
                    score = Decimal(str(request.data.get("score")))
                except InvalidOperation:
                    raise serializers.ValidationError({"score": "نمره باید عدد باشد."})
                if score < 0 or score > 20:
                    raise serializers.ValidationError({"score": "نمره بین ۰ تا ۲۰ باشد."})
                g.score = score
                g.recorded_by = user
                g.save(update_fields=["score", "recorded_by"])
            st.teacher_note = str(request.data.get("note") or "")[:1000]
            if st.status in (ExamScoreStatus.APPEALED, ExamScoreStatus.UNLOCKED):
                st.status = ExamScoreStatus.REVIEWED
            st.save()
            audit(request, "exam.score.review", f"exam:{exam.pk} student:{sid}")
            return Response({"ok": True, "status": st.status})

        if action == "approve":
            if not self._manager(user, exam):
                raise PermissionDenied("تأیید نهایی نمره را معاون آموزشی یا مدیر مدرسه انجام می‌دهد.")
            st = _status(exam, sid)
            if st.status == ExamScoreStatus.APPEALED:
                raise serializers.ValidationError({"status": "ابتدا اعتراض را دبیر بررسی کند."})
            if _grade(exam, sid) is None:
                raise serializers.ValidationError({"student": "نمره‌ای برای تأیید وجود ندارد."})
            from django.utils import timezone
            st.status, st.approved_by, st.approved_at = ExamScoreStatus.FINAL, user, timezone.now()
            st.save()
            audit(request, "exam.score.approve", f"exam:{exam.pk} student:{sid}")
            return Response({"ok": True, "status": st.status})

        if action == "unlock":
            if user.role not in UNLOCKERS:
                raise PermissionDenied("بازکردن قفل نمره‌ی نهایی فقط با مدیر مدرسه یا مدیر سیستم است.")
            reason = str(request.data.get("reason") or "").strip()[:300]
            if not reason:
                raise serializers.ValidationError({"reason": "دلیل بازکردن قفل را بنویسید."})
            st = _status(exam, sid)
            if st.status != ExamScoreStatus.FINAL:
                raise serializers.ValidationError({"status": "فقط نمره‌ی نهایی قفل دارد."})
            st.status, st.unlock_reason = ExamScoreStatus.UNLOCKED, reason
            st.save()
            audit(request, "exam.score.unlock", f"exam:{exam.pk} student:{sid}")
            return Response({"ok": True, "status": st.status})

        raise serializers.ValidationError({"action": "عملیات نامعتبر است."})


class MyExamScoresView(APIView):
    """GET /api/exams/my-scores/ — نمره‌ی موقت یا نهایی خود دانش‌آموز در هر آزمون، با وضعیت و امکان اعتراض."""

    def get(self, request):
        grades = ClassGrade.objects.filter(student=request.user, period="final").select_related("course")
        exams = {e.title: e for e in Exam.objects.filter(course__in={g.course_id for g in grades})}
        out = []
        for g in grades:
            exam = exams.get(g.title)
            if exam is None:
                continue
            st = ExamScoreStatus.objects.filter(exam=exam, student=request.user).first()
            status = st.status if st else ExamScoreStatus.TEMPORARY
            out.append({"exam": exam.pk, "title": exam.title, "score": float(g.score),
                        "status": status, "status_label": dict(ExamScoreStatus.STATUS_CHOICES).get(status, ""),
                        "can_appeal": status in (ExamScoreStatus.TEMPORARY, ExamScoreStatus.REVIEWED),
                        "appeal": st.appeal_text if st else ""})
        return Response(out)


def exam_for_grade(course, title, period):
    """اگر این نمره‌ی دفتر نمرات نمره‌ی یک آزمون است، آن آزمون؛ وگرنه None."""
    if period != "final":
        return None
    return Exam.objects.filter(course=course, title=title).first()


def grade_book_block(course, title, period, student_id):
    """متن خطا اگر نمره‌ی این دانش‌آموز برای این آزمون قفل است یا تغییر مجاز نیست؛ وگرنه None."""
    exam = exam_for_grade(course, title, period)
    if exam is None:
        return None
    if is_locked(exam, student_id):
        return "نمره‌ی نهایی این آزمون تأیید شده و قفل است؛ مدیر مدرسه باید قفل را باز کند."
    return None


def grade_book_changed(course, title, period, student_id):
    """یک نمره‌ی آزمون در دفتر نمرات تغییر کرد: اگر در انتظار اعتراض یا اصلاح بود، به «بررسی‌شده» می‌رود."""
    exam = exam_for_grade(course, title, period)
    if exam is None:
        return
    st = ExamScoreStatus.objects.filter(exam=exam, student_id=student_id).first()
    if st and st.status in (ExamScoreStatus.APPEALED, ExamScoreStatus.UNLOCKED):
        st.status = ExamScoreStatus.REVIEWED
        st.save(update_fields=["status"])
