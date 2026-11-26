from django.core.exceptions import ValidationError
from django.db import models

WEEKDAY_CHOICES = [(0, "شنبه"), (1, "یکشنبه"), (2, "دوشنبه"), (3, "سه‌شنبه"), (4, "چهارشنبه"), (5, "پنجشنبه"), (6, "جمعه")]


class BellPeriod(models.Model):
    """A school's bell schedule: period number -> start/end time (same for every class of the school)."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="bell_periods",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    number = models.PositiveSmallIntegerField("شماره زنگ")
    start_time = models.TimeField("شروع")
    end_time = models.TimeField("پایان")

    @property
    def minutes(self):
        from .hours import minutes_between
        return minutes_between(self.start_time, self.end_time)

    @property
    def hours(self):
        """ساعت این زنگ (۳۰ تا ۶۰ دقیقه = ۱ ساعت، ۶۱ تا ۹۰ = ۲ ساعت)."""
        from .hours import bell_hours
        return bell_hours(self.start_time, self.end_time)

    class Meta:
        verbose_name = "زنگ"
        verbose_name_plural = "زنگ‌های مدرسه"
        ordering = ["school_id", "number"]
        constraints = [models.UniqueConstraint(fields=["school", "number"], name="uniq_bell_number")]

    def __str__(self):
        return f"زنگ {self.number} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"

    def clean(self):
        if not 1 <= (self.number or 0) <= 12:
            raise ValidationError({"number": "شماره زنگ باید بین ۱ تا ۱۲ باشد."})
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "پایان باید بعد از شروع باشد."})
        if self.school_id and self.start_time and self.end_time:
            clash = BellPeriod.objects.filter(school_id=self.school_id, start_time__lt=self.end_time,
                                              end_time__gt=self.start_time).exclude(pk=self.pk)
            if clash.exists():
                raise ValidationError("زمان این زنگ با زنگ دیگری هم‌پوشانی دارد.")


class ScheduleEntry(models.Model):
    """One cell of a class's weekly timetable. The class belongs to one academic year, so schedules are per year."""
    classroom = models.ForeignKey("academics.ClassRoom", on_delete=models.CASCADE, related_name="schedule",
                                  verbose_name="کلاس")
    weekday = models.PositiveSmallIntegerField("روز هفته", choices=WEEKDAY_CHOICES)
    period = models.ForeignKey(BellPeriod, on_delete=models.PROTECT, related_name="entries", verbose_name="زنگ")
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="schedule", verbose_name="درس")
    room = models.CharField("مکان / کلاس", max_length=50, blank=True)

    class Meta:
        verbose_name = "ردیف برنامه هفتگی"
        verbose_name_plural = "برنامه هفتگی کلاس‌ها"
        constraints = [models.UniqueConstraint(fields=["classroom", "weekday", "period"], name="uniq_schedule_cell")]
        indexes = [models.Index(fields=["classroom", "weekday"])]

    def clean(self):
        errors = {}
        if self.period_id and self.classroom_id and self.period.school_id != self.classroom.school_id:
            errors["period"] = "زنگ باید متعلق به مدرسه همین کلاس باشد."
        if self.course_id and self.classroom_id and self.course.classroom_id != self.classroom_id:
            errors["course"] = "این درس به این کلاس وصل نیست."
        if not errors and self.course_id and self.period_id:
            other = ScheduleEntry.objects.filter(course__teacher_id=self.course.teacher_id, weekday=self.weekday,
                                                 period_id=self.period_id).exclude(pk=self.pk).exclude(classroom_id=self.classroom_id)
            if other.exists():
                errors["course"] = "این معلم در همین روز و زنگ در کلاس دیگری برنامه دارد."
        if errors:
            raise ValidationError(errors)


class TeacherAvailability(models.Model):
    """ساعات حضور دبیر: the days and bell periods a teacher is present in school."""
    teacher = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="availability", verbose_name="دبیر")
    weekday = models.PositiveSmallIntegerField("روز هفته", choices=WEEKDAY_CHOICES)
    period = models.ForeignKey(BellPeriod, on_delete=models.CASCADE, related_name="teacher_availability", verbose_name="زنگ")

    class Meta:
        verbose_name = "ساعت حضور دبیر"
        constraints = [models.UniqueConstraint(fields=["teacher", "weekday", "period"], name="uniq_teacher_availability")]


class Workshop(models.Model):
    """کارگاه مدرسه: how many classes can use it in the same period, and which fields (رشته‌ها) it serves."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="workshops",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    name = models.CharField("نام کارگاه", max_length=120)
    capacity = models.PositiveSmallIntegerField("تعداد کلاس هم‌زمان", default=1)
    fields = models.ManyToManyField("academics.StudyField", blank=True, related_name="workshops", verbose_name="رشته‌ها")

    class Meta:
        verbose_name = "کارگاه"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_workshop_name_per_school")]

    def __str__(self):
        return self.name


class ClassHardLesson(models.Model):
    """دروس سخت یک کلاس، که مدیر یا معاون پیش از برنامه‌ریزی انتخاب می‌کند (سختی ۵ برای این کلاس)."""
    classroom = models.ForeignKey("academics.ClassRoom", on_delete=models.CASCADE, related_name="hard_lessons", verbose_name="کلاس")
    subject = models.ForeignKey("academics.Subject", on_delete=models.CASCADE, related_name="hard_in_classes", verbose_name="درس")

    class Meta:
        verbose_name = "درس سخت کلاس"
        constraints = [models.UniqueConstraint(fields=["classroom", "subject"], name="uniq_hard_lesson_per_class")]


class TimetableVersion(models.Model):
    """یک نسخه‌ی برنامه‌ی هفتگی مدرسه: پیش‌نویس ← در انتظار بررسی ← تأییدشده (یا بایگانی)."""
    DRAFT, REVIEW, APPROVED, ARCHIVED = "draft", "review", "approved", "archived"
    STATUS = [(DRAFT, "پیش‌نویس"), (REVIEW, "در انتظار بررسی"), (APPROVED, "تأییدشده"), (ARCHIVED, "بایگانی")]
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="timetable_versions", verbose_name="مدرسه")
    title = models.CharField("عنوان نسخه", max_length=150, blank=True, default="")
    status = models.CharField("وضعیت", max_length=10, choices=STATUS, default=DRAFT)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="ساخته‌شده توسط")
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="تأییدکننده")
    approved_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField("یادداشت بررسی", blank=True, default="")

    class Meta:
        verbose_name = "نسخه‌ی برنامه"
        ordering = ["-created_at"]


class TimetableEntry(models.Model):
    """یک خانه‌ی برنامه: کلاس، روز، زنگ، درس، دبیر و (در صورت کارگاهی) کارگاه."""
    version = models.ForeignKey(TimetableVersion, on_delete=models.CASCADE, related_name="entries", verbose_name="نسخه")
    classroom = models.ForeignKey("academics.ClassRoom", on_delete=models.CASCADE, related_name="timetable_entries", verbose_name="کلاس")
    weekday = models.PositiveSmallIntegerField("روز هفته", choices=WEEKDAY_CHOICES)
    period = models.ForeignKey(BellPeriod, on_delete=models.CASCADE, related_name="timetable_entries", verbose_name="زنگ")
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT, related_name="timetable_entries", verbose_name="درس")
    teacher = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="timetable_entries", verbose_name="دبیر")
    workshop = models.ForeignKey(Workshop, null=True, blank=True, on_delete=models.SET_NULL, related_name="entries", verbose_name="کارگاه")
    # نیمه‌ی زنگ: ۰ = زنگ کامل یا نیمه‌ی اول؛ ۱ = نیمه‌ی دوم زنگ ۲ ساعته (دو درس در یک زنگ). برنامه‌های قدیمی همه ۰ هستند.
    part = models.PositiveSmallIntegerField("نیمه‌ی زنگ", default=0)
    # ساعت این جلسه (۱ یا ۲ ساعت). برنامه‌های قدیمی صفر دارند و از ساعت زنگ حساب می‌شوند.
    piece_hours = models.PositiveSmallIntegerField("ساعت این جلسه", default=0)
    # روز اضافه: این جلسه از روزهای مجاز دبیر فراتر است (برای دروس کم‌ساعت) و باید در برنامه مشخص باشد.
    extra_day = models.BooleanField("روز اضافه‌ی دبیر", default=False)

    class Meta:
        verbose_name = "خانه‌ی برنامه"
        constraints = [
            models.UniqueConstraint(fields=["version", "classroom", "weekday", "period", "part"], name="uniq_entry_class_slot_part"),
        ]


class TimetableChange(models.Model):
    """تاریخچه‌ی هر تغییر در یک نسخه‌ی برنامه: ساخت، تلاش دوباره، افزودن و حذف دستی."""
    version = models.ForeignKey(TimetableVersion, on_delete=models.CASCADE, related_name="changes", verbose_name="نسخه")
    user = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="کاربر")
    action = models.CharField("عملیات", max_length=30)
    detail = models.CharField("شرح", max_length=400, blank=True, default="")
    # وضعیت جلسه‌های نسخه پیش از این تغییر (برای بازگرداندن)
    before = models.JSONField("وضعیت قبل از تغییر", null=True, blank=True)
    at = models.DateTimeField("زمان", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "تاریخچه‌ی برنامه"
        ordering = ["-at"]


def snapshot(version):
    """وضعیت کامل جلسه‌های یک نسخه به شکل فهرست ساده (برای بازگرداندن)."""
    return [{"classroom": e.classroom_id, "weekday": e.weekday, "period": e.period_id, "subject": e.subject_id,
             "teacher": e.teacher_id, "workshop": e.workshop_id, "part": e.part, "piece_hours": e.piece_hours,
             "extra_day": e.extra_day}
            for e in TimetableEntry.objects.filter(version=version)]


def log_change(version, user, action, detail="", before=None):
    TimetableChange.objects.create(version=version, user=user if getattr(user, "pk", None) else None,
                                   action=action, detail=str(detail)[:400], before=before)
