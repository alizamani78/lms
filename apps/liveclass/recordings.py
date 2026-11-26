"""ضبط جلسات بیگ‌بلو: خواندن فهرست ضبط‌ها، ثبت محلی، و حذف از سرور."""
import datetime as dt
import xml.etree.ElementTree as ET

from django.utils import timezone

from .providers import BigBlueButton, _credentials, _http_get
from .models import ClassRecording


def _ms_to_dt(ms):
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_remote(session):
    """فهرست ضبط‌های این جلسه را از سرور بیگ‌بلو می‌گیرد: [{remote_id, playback_url, recorded_at}]."""
    base, secret = _credentials(session)
    if not (base and secret):
        raise RuntimeError("BBB is not configured")
    provider = BigBlueButton()
    url = provider._signed("getRecordings", {"meetingID": session.external_id}, base, secret)
    root = ET.fromstring(_http_get(url))
    if (root.findtext("returncode") or "").upper() != "SUCCESS":
        raise RuntimeError(root.findtext("message") or "getRecordings failed")
    out = []
    for rec in root.iter("recording"):
        rid = (rec.findtext("recordID") or "").strip()
        if not rid:
            continue
        playback = ""
        for fmt in rec.iter("format"):
            u = (fmt.findtext("url") or "").strip()
            if u:
                playback = u
                break
        out.append({"remote_id": rid, "playback_url": playback, "recorded_at": _ms_to_dt(rec.findtext("startTime"))})
    return out


DEFAULT_RETENTION_DAYS = 2   # پیش‌فرض: ۲ روز


def sync(session, retention_days=DEFAULT_RETENTION_DAYS):
    """ضبط‌های تازه را ثبت می‌کند؛ ضبط‌های موجود دست نمی‌خورند."""
    made = 0
    for item in fetch_remote(session):
        obj, created = ClassRecording.objects.get_or_create(
            session=session, remote_id=item["remote_id"],
            defaults={"playback_url": item["playback_url"], "recorded_at": item["recorded_at"],
                      "retention_days": retention_days,
                      "expires_at": (item["recorded_at"] or timezone.now()) + dt.timedelta(days=retention_days)})
        if created:
            made += 1
    return made


def set_retention(rec, days):
    """مدت اعتبار را تغییر می‌دهد؛ تاریخ حذف از زمان ضبط (یا همین حالا) حساب می‌شود."""
    rec.retention_days = days
    base = rec.recorded_at or timezone.now()
    rec.expires_at = base + dt.timedelta(days=days)
    rec.save(update_fields=["retention_days", "expires_at"])


WARN_BEFORE = dt.timedelta(hours=12)


def warn_expiring(rec):
    """پیش از حذف خودکار، به دبیر و مدیر و معاون هشدار می‌دهد (پیامک از طریق صف ارسال پیامک مدرسه)."""
    from apps.accounts.models import User
    from apps.accounts.models import SchoolMembership
    from apps.sms.notify import enqueue_user
    school = rec.session.course.org
    roles = ["school_admin", "school_deputy_edu"]
    people = list(User.objects.filter(
        pk__in=SchoolMembership.objects.filter(school=school, user__role__in=roles).values_list("user_id", flat=True)))
    if rec.session.course.teacher_id:
        people.append(rec.session.course.teacher)
    text = (f"ضبط جلسه «{rec.session.title[:40]}» تا {rec.expires_at:%Y/%m/%d} حذف می‌شود. "
            "در صورت نیاز، از سامانه مدت اعتبار را تمدید کنید.")
    for person in people:
        enqueue_user(person, school, "recording_expiry", text, key=f"rec-expiry:{rec.pk}")
    rec.warned_at = timezone.now()
    rec.save(update_fields=["warned_at"])


def delete_remote(rec):
    """ضبط را از سرور بیگ‌بلو حذف می‌کند و وضعیت را «حذف‌شده» می‌گذارد. خطای موقت را بالا می‌دهد تا بعداً دوباره تلاش شود."""
    base, secret = _credentials(rec.session)
    if not (base and secret):
        raise RuntimeError("BBB is not configured")
    provider = BigBlueButton()
    url = provider._signed("deleteRecordings", {"recordID": rec.remote_id}, base, secret)
    root = ET.fromstring(_http_get(url))
    if (root.findtext("returncode") or "").upper() != "SUCCESS":
        raise RuntimeError(root.findtext("message") or "deleteRecordings failed")
    rec.status = "deleted"
    rec.deleted_at = timezone.now()
    rec.save(update_fields=["status", "deleted_at"])
