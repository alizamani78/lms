from django.conf import settings
from django.db import models


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "حاضر"
        ABSENT = "absent", "غایب"
        LATE = "late", "تأخیر"
        EXCUSED = "excused", "غیبت موجه"
        LEAVE = "leave", "مرخصی"

    class Source(models.TextChoices):
        MANUAL = "manual", "ثبت دستی"
        ONLINE = "online_provider", "گزارش سرویس کلاس آنلاین"
        IMPORT = "import", "درون‌ریزی"

    class Validation(models.TextChoices):
        MANUAL = "manual", "ثبت‌شده توسط کاربر"
        VALIDATED = "validated", "اعتبارسنجی‌شده"
        REVIEW = "needs_review", "نیازمند بررسی"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attendance",
                                verbose_name="دانش‌آموز")
    classroom = models.ForeignKey("academics.ClassRoom", on_delete=models.CASCADE, related_name="attendance",
                                  verbose_name="کلاس")
    course = models.ForeignKey("courses.Course", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="attendance", verbose_name="درس (برای حضور زنگی/جلسه‌ای)")
    session = models.ForeignKey("liveclass.ClassSession", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="attendance", verbose_name="جلسه آنلاین")
    date = models.DateField("تاریخ")
    period = models.PositiveSmallIntegerField("زنگ", null=True, blank=True)
    slot_key = models.CharField(max_length=24, editable=False)       # c<course>p<period>; 0 when not used
    status = models.CharField("وضعیت", max_length=8, choices=Status.choices)
    online_seconds = models.PositiveIntegerField("مدت حضور در کلاس آنلاین (ثانیه)", default=0)
    minutes_late = models.PositiveSmallIntegerField("دقیقه تأخیر", default=0)
    note = models.CharField("توضیح", max_length=200, blank=True)
    source = models.CharField("منبع", max_length=16, choices=Source.choices, default=Source.MANUAL)
    validation = models.CharField("اعتبارسنجی", max_length=12, choices=Validation.choices, default=Validation.MANUAL)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    received_at = models.DateTimeField("زمان ثبت", auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # the absence in hours and minutes, from the bell's start and end (the whole day is the sum of its bells)
    absence_minutes = models.PositiveSmallIntegerField("دقیقه‌ی غیبت", default=0)
    absence_hours = models.PositiveSmallIntegerField("ساعت غیبت", default=0)

    class Meta:
        verbose_name = "رکورد حضور و غیاب"
        verbose_name_plural = "حضور و غیاب"
        constraints = [models.UniqueConstraint(fields=["student", "classroom", "date", "slot_key"],
                                               name="one_attendance_per_slot")]
        indexes = [models.Index(fields=["classroom", "date"]), models.Index(fields=["student", "date"]),
                   models.Index(fields=["date", "status"])]


def slot_key(course_id, period):
    return f"c{course_id or 0}p{period or 0}"


class AttendanceChange(models.Model):
    """Every correction of an attendance record: who, when, why."""
    record = models.ForeignKey(AttendanceRecord, on_delete=models.CASCADE, related_name="changes")
    old_status = models.CharField(max_length=8)
    new_status = models.CharField(max_length=8)
    reason = models.CharField(max_length=300)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "اصلاح حضور و غیاب"
        verbose_name_plural = "اصلاحات حضور و غیاب"


class TeacherAttendance(models.Model):
    """Daily presence of a teacher at a school (one row per teacher, school and day)."""
    class Status(models.TextChoices):
        PRESENT = "present", "حاضر"
        ABSENT = "absent", "غایب"
        LATE = "late", "تأخیر"
        LEAVE = "leave", "مرخصی / موجه"

    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="teacher_attendance",
                                limit_choices_to={"role": "teacher"}, verbose_name="معلم")
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="teacher_attendance",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    date = models.DateField("تاریخ")
    status = models.CharField("وضعیت", max_length=8, choices=Status.choices)
    note = models.CharField("توضیح", max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "حضور معلم"
        verbose_name_plural = "حضور معلمان"
        constraints = [models.UniqueConstraint(fields=["teacher", "school", "date"], name="one_teacher_day")]
        indexes = [models.Index(fields=["school", "date"])]
