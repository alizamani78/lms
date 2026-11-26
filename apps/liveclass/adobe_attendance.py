"""حضور دانش‌آموزان در Adobe Connect: از روی گزارش حضور جلسه، مدت حضور هر دانش‌آموز در OnlinePresence ذخیره می‌شود
(همان جدولی که حضور BigBlueButton هم در آن است؛ پس کارنامه و گزارش حضور از یک‌جا خوانده می‌شوند)."""
import datetime

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import ClassSession, OnlinePresence

User = get_user_model()


def sync_session(session):
    """حضور یک جلسه‌ی Adobe Connect را از سرور می‌خواند و ذخیره می‌کند. تعداد دانش‌آموزان به‌روز شده را برمی‌گرداند."""
    from . import adobe
    if session.provider != "adobe" or not session.external_id or session.system is None:
        return 0
    system = session.system
    cookie = adobe.login(system.base_url, system.admin_user, system.get_admin_password())
    rows = adobe.attendance_rows(system.base_url, cookie, session.external_id)
    updated = 0
    for login_name, seconds in rows:
        from apps.integrations.models import AdobeAccount
        acc = AdobeAccount.objects.filter(system=session.system, login=login_name).select_related("user").first()
        student = acc.user if acc else User.objects.filter(Q(username=login_name) | Q(student_code=login_name), role="student").first()
        if student is None:
            continue
        rec, _ = OnlinePresence.objects.get_or_create(session=session, student=student)
        if seconds > rec.total_seconds:
            rec.total_seconds = seconds
            rec.last_seen = timezone.now()
            rec.save(update_fields=["total_seconds", "last_seen"])
            updated += 1
    return updated


def sync_recent_sessions(days=3):
    """برای جلسه‌های Adobe Connect که در چند روز اخیر تمام شده‌اند، حضور را دوباره می‌خواند."""
    since = timezone.now() - datetime.timedelta(days=days)
    total = 0
    for s in ClassSession.objects.filter(provider="adobe", ends_at__gte=since, ends_at__lte=timezone.now()).exclude(external_id=""):
        try:
            total += sync_session(s)
        except Exception:  # one session must not stop the others
            continue
    return total
