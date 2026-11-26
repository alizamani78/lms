import csv

from django.core.cache import cache
from datetime import timedelta

from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import OrgUnit, User
from apps.core.jalali import jalali_str
from apps.core.models import audit
from apps.core.permissions import role_required
from apps.core.scope import org_in_scope
from apps.courses.models import Course, Enrollment
from apps.exams.authoring import staff_exams
from apps.exams.models import Answer, Attempt, Question

from .models import DailyOrgStats


class ReportView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "report"
    permission_classes = [role_required(*User.READ_ROLES)]


class ExamReportView(ReportView):
    def get(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)      # authorisation first, then cache
        key = f"rep:exam:{exam.pk}"
        data = cache.get(key)
        if data is None:
            rows = list(Attempt.objects.filter(exam=exam).values_list("score", "total", "submitted_at"))
            pcts = [100.0 * float(s) / float(t) for s, t, at in rows if at is not None and t]
            hist = [0] * 10
            for p in pcts:
                hist[min(int(p // 10), 9)] += 1
            qstats = {r["question_id"]: r for r in
                      Answer.objects.filter(attempt__exam=exam, attempt__submitted_at__isnull=False)
                      .values("question_id").annotate(answered=Count("id"), correct=Count("id", filter=Q(choice__is_correct=True)))}
            questions = []
            for q in Question.objects.filter(exam=exam).values("id", "text"):
                st = qstats.get(q["id"], {"answered": 0, "correct": 0})
                questions.append({"id": q["id"], "text": q["text"][:80], "answered": st["answered"],
                                  "correct_rate": round(100.0 * st["correct"] / st["answered"], 1) if st["answered"] else None})
            data = {
                "exam": exam.pk,
                "enrolled": Enrollment.objects.filter(course_id=exam.course_id).count(),
                "started": len(rows),
                "submitted": len(pcts),
                "avg_percent": round(sum(pcts) / len(pcts), 1) if pcts else None,
                "min_percent": round(min(pcts), 1) if pcts else None,
                "max_percent": round(max(pcts), 1) if pcts else None,
                "histogram_10pct_buckets": hist,
                "questions": questions,
            }
            cache.set(key, data, 60)
        return Response(data)


class _Echo:
    def write(self, value):
        return value


def _safe(v):
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR at the start of a cell)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


class ExamResultsCsvView(ReportView):
    def get(self, request, pk):
        exam = get_object_or_404(staff_exams(request.user), pk=pk)
        rows = (Attempt.objects.filter(exam=exam).order_by("student__username")
                .values_list("student__username", "student__last_name", "student__first_name",
                             "submitted_at", "score", "total"))
        writer = csv.writer(_Echo())

        def gen():
            yield "\ufeff"
            yield writer.writerow(["national_id", "last_name", "first_name", "submitted_at", "submitted_at_jalali", "score", "total"])
            for r in rows.iterator(chunk_size=1000):
                yield writer.writerow([_safe(r[0]), _safe(r[1]), _safe(r[2]),
                                       r[3].isoformat() if r[3] else "", jalali_str(r[3], True) if r[3] else "",
                                       r[4] if r[4] is not None else "",
                                       r[5] if r[5] is not None else ""])

        audit(request, "report.csv", f"exam:{exam.pk}")
        resp = StreamingHttpResponse(gen(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="exam-{exam.pk}.csv"'
        return resp


class OrgSummaryView(ReportView):
    """Rollup per direct child of an org (province→regions, region→schools, school→itself).
    Four grouped queries regardless of size; cached 5 minutes."""
    permission_classes = [role_required(*User.ADMIN_ROLES)]

    def get(self, request, pk):
        org = get_object_or_404(OrgUnit, pk=pk)
        if not org_in_scope(request.user, org):
            return Response(status=404)
        key = f"rep:org:{org.pk}"
        data = cache.get(key)
        if data is None:
            data = self._build(org)
            cache.set(key, data, 300)
        return Response(data)

    def _build(self, org):
        schools = dict(OrgUnit.objects.filter(kind=OrgUnit.Kind.SCHOOL, path__startswith=org.path)
                       .values_list("id", "path"))
        depth = len(org.path.strip("/").split("/"))

        def child_of(school_id):
            segs = schools[school_id].strip("/").split("/")
            return int(segs[depth]) if len(segs) > depth else org.pk

        ids = list(schools)
        students = dict(User.objects.filter(role=User.Role.STUDENT, org_id__in=ids)
                        .values_list("org_id").annotate(n=Count("id")))
        courses = dict(Course.objects.filter(org_id__in=ids).values_list("org_id").annotate(n=Count("id")))
        att = {r["exam__course__org_id"]: r for r in
               Attempt.objects.filter(submitted_at__isnull=False, exam__course__org_id__in=ids)
               .values("exam__course__org_id").annotate(n=Count("id"), s=Sum("score"), t=Sum("total"))}
        agg = {}
        for sid in ids:
            c = agg.setdefault(child_of(sid), {"schools": 0, "students": 0, "courses": 0, "attempts": 0, "s": 0, "t": 0})
            c["schools"] += 1
            c["students"] += students.get(sid, 0)
            c["courses"] += courses.get(sid, 0)
            a = att.get(sid)
            if a:
                c["attempts"] += a["n"]
                c["s"] += float(a["s"] or 0)
                c["t"] += float(a["t"] or 0)
        names = dict(OrgUnit.objects.filter(pk__in=agg).values_list("id", "name"))
        out = []
        for cid, c in sorted(agg.items()):
            out.append({"org": cid, "name": names.get(cid, ""), "schools": c["schools"], "students": c["students"],
                        "courses": c["courses"], "submitted_attempts": c["attempts"],
                        "avg_percent": round(100.0 * c["s"] / c["t"], 1) if c["t"] else None})
        return {"org": org.pk, "name": org.name, "children": out,
                "totals": {k: sum(r[k] for r in out) for k in ("schools", "students", "courses", "submitted_attempts")}}


class OrgStatsView(ReportView):
    """GET /api/reports/orgs/<id>/stats/?days=30 — rollup from the pre-computed daily table (fast at province scale)."""
    permission_classes = [role_required(*User.READ_ROLES)]

    def get(self, request, pk):
        org = get_object_or_404(OrgUnit, pk=pk)
        if not org_in_scope(request.user, org):
            return Response(status=404)
        try:
            days = min(max(int(request.query_params.get("days", 30)), 1), 366)
        except ValueError:
            days = 30
        key = f"rep:orgstats:{org.pk}:{days}"
        data = cache.get(key)
        if data is None:
            data = self._build(org, days)
            cache.set(key, data, 300)
        return Response(data)

    def _build(self, org, days):
        schools = dict(OrgUnit.objects.filter(kind="school", path__startswith=org.path).values_list("id", "path"))
        depth = len(org.path.strip("/").split("/"))

        def child_of(sid):
            segs = schools[sid].strip("/").split("/")
            return int(segs[depth]) if len(segs) > depth else org.pk

        since = timezone.localdate() - timedelta(days=days)
        ids = list(schools)
        sums = {r["org_id"]: r for r in DailyOrgStats.objects.filter(org_id__in=ids, date__gte=since)
                .values("org_id").annotate(att_total=Sum("att_total"), att_present=Sum("att_present"),
                                           exams=Sum("exams_submitted"), ssum=Sum("score_sum"),
                                           stot=Sum("score_total"), subs=Sum("submissions"),
                                           sessions=Sum("sessions"), failed=Sum("sessions_failed"))}
        latest = DailyOrgStats.objects.filter(org_id__in=ids).aggregate(d=Max("date"))["d"]
        students = dict(DailyOrgStats.objects.filter(org_id__in=ids, date=latest).values_list("org_id", "students")) if latest else {}
        agg = {}
        for sid in ids:
            c = agg.setdefault(child_of(sid), dict(schools=0, students=0, att_total=0, att_present=0, exams=0,
                                                   ssum=0.0, stot=0.0, subs=0, sessions=0, failed=0))
            c["schools"] += 1
            c["students"] += students.get(sid, 0)
            r = sums.get(sid)
            if r:
                for k in ("att_total", "att_present", "exams", "subs", "sessions", "failed"):
                    c[k] += r[k] or 0
                c["ssum"] += float(r["ssum"] or 0)
                c["stot"] += float(r["stot"] or 0)
        names = dict(OrgUnit.objects.filter(pk__in=agg).values_list("id", "name"))
        children = [{"org": cid, "name": names.get(cid, ""), "schools": c["schools"], "students": c["students"],
                     "attendance_rate": round(100.0 * c["att_present"] / c["att_total"], 1) if c["att_total"] else None,
                     "exams_submitted": c["exams"],
                     "avg_exam_percent": round(100.0 * c["ssum"] / c["stot"], 1) if c["stot"] else None,
                     "assignment_submissions": c["subs"], "online_sessions": c["sessions"],
                     "online_session_failure_rate": round(100.0 * c["failed"] / c["sessions"], 1) if c["sessions"] else None}
                    for cid, c in sorted(agg.items())]
        return {"org": org.pk, "name": org.name, "days": days, "data_through": latest, "children": children}
