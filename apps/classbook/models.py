"""دفتر فرایندی کلاس: طرح درس سالانه، طرح درس روزانه و خلاصه‌ی فعالیت هر جلسه.

همه‌ی کاربرگ‌ها یک چرخه دارند: پیش‌نویس ← ارسال برای تأیید ← تأییدشده (قفل).
پس از تأیید مدیر یا معاون آموزشی، تغییر مگر با بازگرداندن به پیش‌نویس ممکن نیست.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Status(models.TextChoices):
    DRAFT = "draft", "پیش‌نویس"
    SUBMITTED = "submitted", "در انتظار تأیید"
    APPROVED = "approved", "تأییدشده (قفل)"


class AnnualPlan(models.Model):
    """طرح درس سالانه (برگ ۶۰۱) برای یک درس یک کلاس."""
    course = models.OneToOneField("courses.Course", on_delete=models.CASCADE, related_name="annual_plan", verbose_name="درس")
    total_sessions = models.PositiveSmallIntegerField("تعداد جلسات سال", null=True, blank=True,
                                                      help_text="خالی بماند تا از واحد درس محاسبه شود؛ معلم می‌تواند تغییر دهد.")
    sessions_per_week = models.PositiveSmallIntegerField("ساعت تدریس در هفته", null=True, blank=True)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.TextField("یادداشت بررسی", blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "طرح درس سالانه"


class AnnualPlanRow(models.Model):
    """یک جلسه در طرح درس سالانه: ماه، شماره‌ی جلسه، روز، تاریخ، صفحات کتاب و عناوین تدریس."""
    plan = models.ForeignKey(AnnualPlan, on_delete=models.CASCADE, related_name="rows")
    index = models.PositiveSmallIntegerField("شماره‌ی جلسه")
    date = models.DateField("تاریخ", null=True, blank=True)
    from_page = models.CharField("از صفحه", max_length=20, blank=True, default="")
    to_page = models.CharField("تا صفحه", max_length=20, blank=True, default="")
    topics = models.TextField("عناوین مورد تدریس", blank=True, default="")

    class Meta:
        ordering = ["index"]
        constraints = [models.UniqueConstraint(fields=["plan", "index"], name="uniq_plan_session_index")]


class DailyPlan(models.Model):
    """طرح درس روزانه (برگ ۶۰۲) برای یک جلسه‌ی یک درس. بخش‌های فرم در data ذخیره می‌شوند."""
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="daily_plans", verbose_name="درس")
    number = models.PositiveSmallIntegerField("شماره‌ی طرح درس")
    date = models.DateField("تاریخ اجرا")
    title = models.CharField("موضوع درس", max_length=200, blank=True, default="")
    duration_minutes = models.PositiveSmallIntegerField("مدت اجرا (دقیقه)", default=45)
    place = models.CharField("مکان", max_length=120, blank=True, default="")
    # بخش‌ها: level_goal, goal, stage_goals, objectives[{level, objective, elements, sphere}], topics, materials,
    # entry_behavior, initial_contact{text,minutes}, grouping, motivation, initial_assessment, method, learners_count
    data = models.JSONField("محتوای فرم", default=dict, blank=True)
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.TextField("یادداشت بررسی", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "number"]
        constraints = [models.UniqueConstraint(fields=["course", "number"], name="uniq_daily_plan_number")]


class SessionSummary(models.Model):
    """خلاصه‌ی فعالیت هر جلسه (برگ ۶۰۹): روز و تاریخ، فعالیت انجام‌شده، تکلیف به دانش‌آموزان."""
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="session_summaries", verbose_name="درس")
    session_no = models.PositiveSmallIntegerField("شماره‌ی جلسه")
    date = models.DateField("تاریخ جلسه")
    activity = models.TextField("فعالیت انجام‌شده", blank=True, default="")
    homework = models.TextField("تکلیف محوله به دانش‌آموزان", blank=True, default="")
    status = models.CharField("وضعیت", max_length=10, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.TextField("یادداشت بررسی", blank=True, default="")

    class Meta:
        ordering = ["session_no"]
        constraints = [models.UniqueConstraint(fields=["course", "session_no"], name="uniq_summary_session_no")]


class ReportCardNote(models.Model):
    """توضیحات مدیر یا معاون برای کارنامه‌ی یک دانش‌آموز در یک نیم‌سال (روی کارنامه چاپ می‌شود)."""
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="report_notes", verbose_name="دانش‌آموز")
    term = models.CharField("نیم‌سال", max_length=10)
    text = models.TextField("توضیحات", blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "توضیحات کارنامه"
        constraints = [models.UniqueConstraint(fields=["student", "term"], name="uniq_report_note")]

