"""قواعد دفتر فرایندی: تعداد جلسات از واحد، قفل تأیید، و ساختن ردیف‌های طرح سالانه."""
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from apps.academics.models import GradeWeeklyHours

from .models import AnnualPlanRow, Status

SCHOOL_WEEKS = 32   # هفته‌ی تدریس در سال (دو نیم‌سال)


def weekly_hours(course):
    """ساعت هفتگی درس از واحد (مصوبات پایه/رشته)؛ اگر نبود، ساعت پیش‌فرض درس."""
    cls = course.classroom
    sub = course.subject
    if sub is None:
        return 1
    gw = None
    if cls is not None:
        gw = GradeWeeklyHours.objects.filter(grade=cls.grade, subject=sub, field=cls.study_field).first() \
            or GradeWeeklyHours.objects.filter(grade=cls.grade, subject=sub, field__isnull=True).first()
    return gw.hours if gw else (sub.weekly_hours or 1)


def plan_totals(plan):
    """تعداد جلسات سال و ساعت هفتگی: مقدار معلم اگر داده شده، وگرنه از واحد."""
    per_week = plan.sessions_per_week or weekly_hours(plan.course)
    total = plan.total_sessions or per_week * SCHOOL_WEEKS
    return per_week, total


def ensure_rows(plan):
    """ردیف‌های کم یا زیاد را با تعداد جلسات تنظیم می‌کند؛ ردیف‌های پرشده حذف نمی‌شوند."""
    _, total = plan_totals(plan)
    existing = set(plan.rows.values_list("index", flat=True))
    AnnualPlanRow.objects.bulk_create([AnnualPlanRow(plan=plan, index=i) for i in range(1, total + 1) if i not in existing])
    extra = plan.rows.filter(index__gt=total, date__isnull=True, topics="")
    extra.delete()
    return total


def assert_editable(obj):
    if obj.status == Status.APPROVED:
        raise PermissionDenied("این کاربرگ تأیید شده و قفل است؛ برای تغییر، مدیر یا معاون آن را به پیش‌نویس برگرداند.")


def submit(obj):
    if obj.status not in (Status.DRAFT,):
        raise ValueError("فقط پیش‌نویس برای تأیید فرستاده می‌شود.")
    obj.status = Status.SUBMITTED
    obj.submitted_at = timezone.now()
    obj.save(update_fields=["status", "submitted_at"])


def approve(obj, user):
    if obj.status != Status.SUBMITTED:
        raise ValueError("فقط کاربرگ در انتظار تأیید قابل تأیید است.")
    obj.status = Status.APPROVED
    obj.approved_by = user
    obj.approved_at = timezone.now()
    obj.save(update_fields=["status", "approved_by", "approved_at"])


def return_to_draft(obj, note):
    if obj.status not in (Status.SUBMITTED, Status.APPROVED):
        raise ValueError("این کاربرگ پیش‌نویس است.")
    obj.status = Status.DRAFT
    obj.note = (note or "")[:2000]
    obj.save(update_fields=["status", "note"])
