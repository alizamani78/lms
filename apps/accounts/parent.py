"""Parent/guardian portal: strictly limited to students linked AND verified by school staff."""
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.attendance.models import AttendanceRecord
from apps.attendance.views import summarize
from apps.coursework.models import Assignment, Submission
from apps.core.permissions import role_required
from apps.exams.models import Attempt

from .models import ParentLink, User

IsParent = role_required(User.Role.PARENT)


def _linked_student(request, student_id):
    link = get_object_or_404(ParentLink.objects.select_related("student__org"),
                             parent=request.user, student_id=student_id, is_verified=True)
    return link.student


class ChildrenView(APIView):
    permission_classes = [IsParent]

    def get(self, request):
        links = ParentLink.objects.filter(parent=request.user, is_verified=True).select_related("student__org")
        return Response([{"id": l.student_id, "username": l.student.username, "name": l.student.get_full_name(),
                          "school": l.student.org.name if l.student.org_id else "", "relation": l.relation}
                         for l in links])


class ChildOverviewView(APIView):
    """Attendance (30 days), assignments and published exam results of ONE linked child."""
    permission_classes = [IsParent]

    def get(self, request, student_id):
        student = _linked_student(request, student_id)
        since = timezone.localdate() - timedelta(days=30)
        att = AttendanceRecord.objects.filter(student=student, date__gte=since)
        recent = att.exclude(status="present").order_by("-date").values("date", "status", "note", "course__title")[:15]
        assignments = list(Assignment.objects.filter(is_published=True, course__enrollments__student=student)
                           .select_related("course").order_by("-due_at")[:30])
        subs = {s.assignment_id: s for s in Submission.objects.filter(student=student, assignment__in=assignments)}
        exams = Attempt.objects.filter(student=student, submitted_at__isnull=False, exam__show_results=True) \
            .select_related("exam").order_by("-submitted_at")[:20]
        from apps.courses.models import Enrollment
        from apps.discipline.views import worksheet as discipline_sheet
        from apps.cultural.models import Competition, CompetitionParticipant, Trip, TripParticipant
        from apps.evaluation.logic import course_percentages
        # grades per course (exams, homework and class marks, in %), the child's discipline book, trips and festivals
        pct = course_percentages([student.org_id], student_ids=[student.pk]) if student.org_id else {}
        grades = []
        for (sid, cid), rec in pct.items():
            items = rec[2] + rec[3]
            if items:
                grades.append({"course": rec[0], "percent": round(sum(items) / len(items), 1)})
        sheet = discipline_sheet(student)
        trips = [{"id": tp.trip_id, "title": tp.trip.title, "destination": tp.trip.destination,
                  "start_date": tp.trip.start_date, "end_date": tp.trip.end_date, "consent": tp.get_consent_display()}
                 for tp in TripParticipant.objects.filter(student=student).select_related("trip")[:30]]
        festivals = []
        for cp in CompetitionParticipant.objects.filter(student=student).select_related("competition")[:30]:
            c = cp.competition
            results = []
            if c.is_published:
                results = [{"rank": r.rank, "name": (r.student.get_full_name() or r.student.username) if r.student_id else r.name,
                            "score": float(r.score) if r.score is not None else None}
                           for r in c.results.select_related("student").order_by("rank")]
            festivals.append({"id": c.pk, "title": c.title, "kind": c.get_kind_display(), "date": c.date,
                              "consent": cp.get_consent_display(), "results": results})
        return Response({
            "discipline": {"score": sheet["score"], "total_points": sheet["total_points"], "base": sheet["base"],
                           "records": sheet["records"][-30:]},
            "grades": grades,
            "trips": trips,
            "festivals": festivals,
            "student": {"id": student.pk, "name": student.get_full_name() or student.username},
            "attendance": {"summary": summarize(att), "recent": list(recent)},
            "assignments": [{"title": a.title, "course": a.course.title, "due_at": a.due_at,
                             "max_points": float(a.max_points),
                             "submitted": a.pk in subs,
                             "points": (float(subs[a.pk].points) if a.pk in subs and subs[a.pk].points is not None else None)}
                            for a in assignments],
            "exams": [{"title": t.exam.title, "score": float(t.score), "total": float(t.total),
                       "pending_review": t.grading_status == "pending"} for t in exams],
        })
