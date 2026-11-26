from datetime import date as date_cls, timedelta

from celery import shared_task
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.accounts.models import OrgUnit, User
from apps.attendance.models import AttendanceRecord
from apps.coursework.models import Submission
from apps.exams.models import Attempt
from apps.liveclass.models import ClassSession

from .models import DailyOrgStats

FIELDS = ["students", "att_total", "att_present", "att_absent", "exams_submitted", "score_sum", "score_total",
          "submissions", "sessions", "sessions_failed"]


@shared_task
def build_daily_stats(day=None):
    """Rebuild one day (default: yesterday) with a handful of GROUP BY queries. Idempotent (upsert)."""
    day = date_cls.fromisoformat(day) if day else timezone.localdate() - timedelta(days=1)
    rows = {sid: {f: 0 for f in FIELDS} for sid in OrgUnit.objects.filter(kind="school").values_list("id", flat=True)}

    def put(school_id, **vals):
        if school_id in rows:
            for k, v in vals.items():
                rows[school_id][k] += v or 0

    for r in User.objects.filter(role="student", is_active=True, org__isnull=False).values("org_id").annotate(n=Count("id")):
        put(r["org_id"], students=r["n"])
    for r in AttendanceRecord.objects.filter(date=day).values("classroom__school_id").annotate(
            n=Count("id"), p=Count("id", filter=Q(status__in=["present", "late"])),
            a=Count("id", filter=Q(status="absent"))):
        put(r["classroom__school_id"], att_total=r["n"], att_present=r["p"], att_absent=r["a"])
    for r in Attempt.objects.filter(submitted_at__date=day).values("exam__course__org_id").annotate(
            n=Count("id"), s=Sum("score"), t=Sum("total")):
        put(r["exam__course__org_id"], exams_submitted=r["n"], score_sum=r["s"], score_total=r["t"])
    for r in Submission.objects.filter(submitted_at__date=day).values("assignment__course__org_id").annotate(n=Count("id")):
        put(r["assignment__course__org_id"], submissions=r["n"])
    for r in ClassSession.objects.filter(starts_at__date=day).values("course__org_id").annotate(
            n=Count("id"), f=Count("id", filter=Q(remote_status="failed"))):
        put(r["course__org_id"], sessions=r["n"], sessions_failed=r["f"])
    DailyOrgStats.objects.bulk_create(
        [DailyOrgStats(date=day, org_id=sid, **vals) for sid, vals in rows.items()],
        update_conflicts=True, unique_fields=["date", "org"], update_fields=FIELDS, batch_size=1000)
    return len(rows)
