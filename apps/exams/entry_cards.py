"""کارت ورود به جلسه: the seat numbers of an exam and the data of the two cards (instructions card and seat card)."""
from django.db.models import Max
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.courses.models import Enrollment

from .authoring import AuthoringView, staff_exams
from .models import ExamSeat


class SeatsView(AuthoringView):
    """POST {start?, room?} — give a candidate number to every enrolled student who has none yet (in name order)."""

    def post(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)
        start = int(request.data.get("start") or 0)
        room = str(request.data.get("room") or "")[:60]
        taken = set(ExamSeat.objects.filter(exam=exam).values_list("student_id", flat=True))
        enrolled = (Enrollment.objects.filter(course=exam.course).select_related("student")
                    .order_by("student__last_name", "student__first_name", "student__id"))
        nxt = max(start, (ExamSeat.objects.filter(exam=exam).aggregate(m=Max("number"))["m"] or 0) + 1, 1)
        made = 0
        for e in enrolled:
            if e.student_id in taken:
                continue
            while ExamSeat.objects.filter(exam=exam, number=nxt).exists():
                nxt += 1
            ExamSeat.objects.create(exam=exam, student=e.student, number=nxt, room=room)
            nxt += 1
            made += 1
        return Response({"created": made, "total": ExamSeat.objects.filter(exam=exam).count()}, status=201)


class EntryCardsView(AuthoringView):
    """GET — the data of every entry card of the exam (the photo is fetched by the page through the access-checked endpoint)."""

    def get(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user).select_related("course__org"), pk=pk)
        seats = {s.student_id: s for s in ExamSeat.objects.filter(exam=exam)}
        rows = []
        for e in Enrollment.objects.filter(course=exam.course).select_related("student").order_by("student__last_name", "student__id"):
            st = e.student
            seat = seats.get(st.pk)
            rows.append({"student": st.pk, "name": st.get_full_name() or st.username, "student_code": st.student_code or "",
                         "national_id": st.national_id or "", "has_photo": bool(st.photo_id),
                         "number": seat.number if seat else None, "room": seat.room if seat else ""})
        local = exam.starts_at
        return Response({"exam": {"title": exam.title, "course": exam.course.title, "school": exam.course.org.name,
                                  "teacher": (exam.course.teacher.get_full_name() or exam.course.teacher.username) if exam.course.teacher_id else "",
                                  "starts_at": local.isoformat() if local else "", "duration_minutes": exam.duration_minutes,
                                  "instructions": exam.instructions or "", "total": float(sum((q.points for q in exam.questions.all()), 0))},
                         "students": rows})
