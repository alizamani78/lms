from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import ClassSession, OnlinePresence, ProviderEvent
from .providers import PROVIDERS, PermanentProviderError, TransientProviderError

MAX_RETRIES = 5


@shared_task(bind=True)
def create_remote_meeting(self, session_id):
    """Create the room at the provider. Idempotent; retries with growing delay; failures end in status=failed."""
    with transaction.atomic():
        session = ClassSession.objects.select_for_update(of=("self",)).select_related("system").get(pk=session_id)
        if session.remote_status == "ready" or not session.auto_create:
            return "skip"
        session.create_attempts += 1
        session.save(update_fields=["create_attempts"])
    provider = PROVIDERS.get(session.provider)
    try:
        if provider is None:
            raise PermanentProviderError("provider not supported")
        provider.create_meeting(session)
    except TransientProviderError as e:
        if self.request.retries >= MAX_RETRIES:
            ClassSession.objects.filter(pk=session_id).update(remote_status="failed", last_error=str(e)[:200])
            return "failed"
        raise self.retry(exc=e, countdown=min(30 * 2 ** self.request.retries, 900))
    except (PermanentProviderError, RuntimeError) as e:
        ClassSession.objects.filter(pk=session_id).update(remote_status="failed", last_error=str(e)[:200])
        return "failed"
    ClassSession.objects.filter(pk=session_id).update(remote_status="ready", last_error="")
    return "ready"


@shared_task
def process_provider_event(event_id):
    with transaction.atomic():
        ev = ProviderEvent.objects.select_for_update(of=("self",)).select_related("session").get(pk=event_id)
        if ev.processed_at is not None:
            return "done"
        if ev.event_type == "meeting_ended" or ev.session_id is None or not ev.user_ext.isdigit():
            ev.status, ev.error = "ignored", ("" if ev.event_type == "meeting_ended" else "no session/user")
        else:
            row, _ = OnlinePresence.objects.select_for_update().get_or_create(
                session=ev.session, student_id=int(ev.user_ext))
            ts = ev.occurred_at
            if ev.event_type == "user_joined" and row.open_since is None:
                row.open_since = ts
                row.first_join = row.first_join or ts
            elif ev.event_type == "user_left" and row.open_since is not None:
                row.total_seconds += max(int((ts - row.open_since).total_seconds()), 0)
                row.open_since, row.last_seen = None, ts
            row.save()
            ev.status = "processed"
        ev.processed_at = timezone.now()
        ev.save(update_fields=["status", "error", "processed_at"])
    return ev.status


@shared_task
def finalize_ended_sessions(batch=100):
    """After a session ended: turn presence into attendance. Never fabricates data the provider did not send."""
    from apps.attendance.models import AttendanceRecord, slot_key
    cutoff = timezone.now() - timedelta(minutes=10)
    n = 0
    for session in ClassSession.objects.filter(ends_at__lt=cutoff, attendance_status="none").exclude(
            remote_status="failed").select_related("course__classroom")[:batch]:
        classroom = session.course.classroom
        if classroom is None:
            ClassSession.objects.filter(pk=session.pk).update(attendance_status="no_class")
            n += 1
            continue
        if not ProviderEvent.objects.filter(session=session).exists():
            ClassSession.objects.filter(pk=session.pk).update(attendance_status="no_data")
            n += 1
            continue
        duration = max((session.ends_at - session.starts_at).total_seconds(), 1)
        presence = {p.student_id: p for p in OnlinePresence.objects.filter(session=session)}
        day, key = timezone.localtime(session.starts_at).date(), slot_key(session.course_id, None)
        records = []
        for sid in classroom.students.values_list("pk", flat=True):
            p = presence.get(sid)
            seconds = 0
            if p:
                seconds = p.total_seconds + (max(int((session.ends_at - p.open_since).total_seconds()), 0)
                                             if p.open_since else 0)
            pct = 100 * seconds / duration
            late_min = int((p.first_join - session.starts_at).total_seconds() // 60) if p and p.first_join else 0
            if pct >= session.min_presence_percent:
                status, valid = ("late" if late_min > 10 else "present"), "validated"
            elif seconds > 0:
                status, valid = "absent", "needs_review"          # partial presence: a human decides
            else:
                status, valid = "absent", "validated"
            records.append(AttendanceRecord(
                student_id=sid, classroom=classroom, course_id=session.course_id, session=session, date=day,
                slot_key=key, status=status, minutes_late=max(late_min, 0) if status == "late" else 0,
                online_seconds=int(seconds),
                source="online_provider", validation=valid,
                note=f"{int(pct)}٪ حضور از {int(duration // 60)} دقیقه"))
        AttendanceRecord.objects.bulk_create(records, ignore_conflicts=True)    # never overwrite a manual record
        ClassSession.objects.filter(pk=session.pk).update(attendance_status="finalized")
        n += 1
    return n


@shared_task(name="apps.liveclass.tasks.purge_expired_recordings")
def purge_expired_recordings():
    """هر ساعت: ضبط‌هایی که مدت اعتبارشان تمام شده از سرور حذف می‌شوند (خطای موقت، دفعه‌ی بعد دوباره)."""
    from django.utils import timezone
    from .models import ClassRecording
    from .recordings import delete_remote
    now = timezone.now()
    done = 0
    for rec in ClassRecording.objects.filter(status="active", expires_at__lte=now).select_related("session__system")[:200]:
        try:
            delete_remote(rec)
            done += 1
        except Exception:
            continue
    return done


@shared_task(name="apps.liveclass.tasks.warn_expiring_recordings")
def warn_expiring_recordings():
    """هشدار به دبیر، مدیر و معاون، ۱۲ ساعت پیش از حذف خودکار هر ضبط (فقط یک‌بار برای هر ضبط)."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import ClassRecording
    from .recordings import WARN_BEFORE, warn_expiring
    now = timezone.now()
    sent = 0
    for rec in ClassRecording.objects.filter(status="active", warned_at__isnull=True,
                                             expires_at__lte=now + WARN_BEFORE, expires_at__gt=now).select_related(
            "session__course__org", "session__course__teacher")[:200]:
        warn_expiring(rec)
        sent += 1
    return sent


ONLINE_HISTORY_DAYS = 365   # سابقه‌ی ورود و خروج و رویدادهای کلاس آنلاین: یک سال


@shared_task(name="apps.liveclass.tasks.purge_old_online_history")
def purge_old_online_history():
    """هر روز: سابقه‌ی حضور آنلاین (ورود/خروج و رویدادهای سرویس) جلساتی که بیش از یک سال از آن‌ها گذشته، حذف می‌شود.
    سابقه‌ی حضور و غیاب کلاسی (که از این سابقه ساخته شده) جداست و همان‌جا نگهداری می‌شود."""
    from .models import OnlinePresence
    cutoff = timezone.now() - timedelta(days=ONLINE_HISTORY_DAYS)
    p, _ = OnlinePresence.objects.filter(session__starts_at__lt=cutoff).delete()
    e, _ = ProviderEvent.objects.filter(session__starts_at__lt=cutoff).delete()
    return {"presence": p, "events": e}



from celery import shared_task as _shared_task  # noqa: E402


@_shared_task(name="apps.liveclass.tasks.sync_adobe_attendance")
def sync_adobe_attendance():
    from .adobe_attendance import sync_recent_sessions
    return {"updated": sync_recent_sessions(days=3)}
