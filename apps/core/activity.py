"""Activity trail for administrators: who logged in, who opened a task or an exam, and what they did afterwards.
Views are recorded once per object every ten minutes (so a student who keeps refreshing does not flood the log)."""
from django.core.cache import cache

from apps.core.models import AuditLog, audit

LABELS = {
    "auth.login": "ورود به سامانه",
    "auth.login_failed": "تلاش ناموفق ورود",
    "assignment.view": "مشاهده تکلیف",
    "assignment.submit": "ارسال پاسخ تکلیف",
    "exam.view": "مشاهده آزمون (توضیحات)",
    "exam.start": "شروع آزمون",
    "exam.submit": "ثبت نهایی آزمون",
    "exam.result_view": "مشاهده نتیجه آزمون",
    "exam.resume": "بازگرداندن به آزمون",
}
VIEW_WINDOW = 600


def seen_once(request, action, target):
    """True the first time in the window for this user, action and object (used for views)."""
    user = getattr(request, "user", None)
    key = f"seen:{getattr(user, 'pk', 0)}:{action}:{target}"
    return cache.add(key, 1, VIEW_WINDOW)


def record_view(request, action, target):
    if seen_once(request, action, target):
        audit(request, action, target)


def activity_q(user):
    """Which activity rows a user may read: everything for the system administrator, the users inside his scope for
    the other administrators, nothing for anybody else."""
    from django.db.models import Q
    from apps.accounts.models import User
    from apps.core.scope import scope_q
    if user.role == User.Role.SUPER_ADMIN or user.is_superuser:
        return Q()
    if user.role in User.ADMIN_ROLES:
        return scope_q(user, "user__org__path")
    return Q(pk__in=[])
