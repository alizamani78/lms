from django.core.validators import MaxValueValidator, MinValueValidator
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class AcademicYear(models.Model):
    name = models.CharField("سال تحصیلی", max_length=20, unique=True)          # e.g. ۱۴۰۵-۱۴۰۶
    starts_on = models.DateField("شروع")
    ends_on = models.DateField("پایان")
    is_current = models.BooleanField("سال جاری", default=False)

    class Meta:
        verbose_name = "سال تحصیلی"
        verbose_name_plural = "سال‌های تحصیلی"
        constraints = [models.UniqueConstraint(fields=["is_current"], condition=Q(is_current=True),
                                               name="only_one_current_year")]

    def clean(self):
        if self.starts_on and self.ends_on and self.ends_on <= self.starts_on:
            raise ValidationError({"ends_on": "پایان باید بعد از شروع باشد."})

    def __str__(self):
        return self.name


class Level(models.Model):
    code = models.CharField("کد مقطع", max_length=10, null=True, blank=True, unique=True)
    name = models.CharField("مقطع", max_length=60, unique=True)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "مقطع تحصیلی"
        verbose_name_plural = "مقاطع تحصیلی"

    def __str__(self):
        return self.name


class Grade(models.Model):
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="grades", verbose_name="مقطع")
    branch = models.ForeignKey("Branch", null=True, blank=True, on_delete=models.PROTECT, related_name="grades", verbose_name="شاخه")
    code = models.CharField("کد پایه", max_length=10, null=True, blank=True, unique=True)
    name = models.CharField("پایه", max_length=60)
    order = models.PositiveSmallIntegerField("ترتیب", default=0)

    class Meta:
        ordering = ["level__order", "order", "id"]
        verbose_name = "پایه تحصیلی"
        verbose_name_plural = "پایه‌های تحصیلی"
        constraints = [models.UniqueConstraint(fields=["level", "name"], name="uniq_grade_per_level")]

    def __str__(self):
        return f"{self.name} ({self.level})"


class Branch(models.Model):
    """شاخه of a level (عمومی، نظری، کاردانش، فنی و حرفه‌ای، فنی حرفه‌ای و کاردانش، حرفه‌ای استثنایی)."""
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="branches", verbose_name="مقطع")
    name = models.CharField("شاخه", max_length=80)

    class Meta:
        verbose_name = "شاخه"
        ordering = ["level", "name"]
        constraints = [models.UniqueConstraint(fields=["level", "name"], name="uniq_branch_per_level")]

    def __str__(self):
        return f"{self.name} ({self.level})"


class StudyField(models.Model):
    code = models.CharField("کد رشته", max_length=20, null=True, blank=True, unique=True)
    name = models.CharField("رشته", max_length=100)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name="fields", verbose_name="شاخه")
    level = models.ForeignKey(Level, null=True, blank=True, on_delete=models.PROTECT,
                              related_name="fields", verbose_name="مقطع (اختیاری)")

    class Meta:
        verbose_name = "رشته تحصیلی"
        verbose_name_plural = "رشته‌های تحصیلی"

    def __str__(self):
        return self.name


class Subject(models.Model):
    """A curriculum subject (سرفصل). One subject can belong to several grades AND several study fields
    (درس‌های مشترک): e.g. «ریاضی» for grades 10-12 and fields ریاضی‌فیزیک + علوم تجربی."""
    name = models.CharField("نام درس", max_length=150)
    KIND = [("normal", "عادی"), ("lab", "آزمایشگاهی"), ("workshop", "کارگاهی"), ("pe", "تربیت‌بدنی")]
    max_per_day = models.PositiveSmallIntegerField("حداکثر تعداد کلاس این درس در روز", default=1, help_text="برای تعداد بیشتر (مثلاً ۲) عدد بزرگ‌تر بگذارید.")
    kind = models.CharField("نوع آموزشی (برای برنامه‌ریزی)", max_length=12, choices=KIND, default="normal")
    difficulty = models.PositiveSmallIntegerField("سختی درس (۱ آسان تا ۵ سخت)", default=3,
                                                  validators=[MinValueValidator(1), MaxValueValidator(5)])
    code = models.CharField("کد درس", max_length=30, unique=True)
    grades = models.ManyToManyField(Grade, related_name="subjects", verbose_name="پایه‌ها")
    study_fields = models.ManyToManyField(StudyField, blank=True, related_name="subjects",
                                          verbose_name="رشته‌ها (برای درس‌های رشته‌ای)")
    weekly_hours = models.PositiveSmallIntegerField("ساعت در هفته", default=2)
    subject_type = models.CharField("نوع درس (انتخاب دانش‌آموز: اجباری، اختیاری …)", max_length=40, blank=True)
    SHARING = [("general", "عمومی (مشترک همه‌ی رشته‌ها)"), ("specialized", "تخصصی (رشته‌ای)")]
    sharing = models.CharField("وضعیت درس (مبنای اشتراک)", max_length=12, choices=SHARING, blank=True, default="")

    class Meta:
        verbose_name = "درس (سرفصل برنامه)"
        verbose_name_plural = "درس‌های برنامه (سرفصل‌ها)"

    def __str__(self):
        return f"{self.name} ({self.code})"

    def link_problems(self, grades, fields):
        """Return a list of Persian problems for a proposed set of grades and fields (empty = valid).
        A field must match the level of every grade it is linked with (no 'ریاضی' for primary grades)."""
        problems = []
        if not grades:
            problems.append("حداقل یک پایه لازم است.")
        levels = {g.level_id for g in grades}
        for f in fields:
            if f.level_id and levels and f.level_id not in levels:
                problems.append(f"رشته «{f.name}» با مقطع پایه‌های انتخابی هم‌خوان نیست.")
        return problems


def subject_links_valid(subject, grades, fields):
    problems = subject.link_problems(grades, fields)
    if problems:
        raise ValidationError(problems)


class ClassRoom(models.Model):  # noqa: E301
    """A class of students in one school for one academic year (e.g. «دهم ریاضی الف»)."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.PROTECT, related_name="classrooms",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="classrooms",
                                      verbose_name="سال تحصیلی")
    name = models.CharField("نام کلاس", max_length=100)
    code = models.CharField("کد کلاس", max_length=20, blank=True)
    capacity = models.PositiveSmallIntegerField("ظرفیت", null=True, blank=True)
    grade = models.ForeignKey(Grade, on_delete=models.PROTECT, related_name="classrooms", verbose_name="پایه")
    study_field = models.ForeignKey(StudyField, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="classrooms", verbose_name="رشته")
    homeroom_teacher = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="homeroom_classes", limit_choices_to={"role": "teacher"},
                                         verbose_name="معلم راهنما")
    students = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="classrooms",
                                      limit_choices_to={"role": "student"}, verbose_name="دانش‌آموزان")

    class Meta:
        verbose_name = "کلاس"
        verbose_name_plural = "کلاس‌ها"
        constraints = [models.UniqueConstraint(fields=["school", "academic_year", "name"], name="uniq_class_name"),
                       models.UniqueConstraint(fields=["school", "academic_year", "grade", "code"], condition=~Q(code=""),
                                               name="uniq_class_code_per_grade")]

    def clean(self):
        errors = {}
        if self.school_id and self.school.kind != "school":
            errors["school"] = "فقط مدرسه قابل انتخاب است."
        if self.study_field_id and self.grade_id and self.study_field.level_id \
                and self.study_field.level_id != self.grade.level_id:
            errors["study_field"] = "رشته با مقطع این پایه هم‌خوان نیست."
        if self.school_id and self.grade_id:
            # a school belongs to ONE level: its classes only use grades of that level (and fields of that level)
            from apps.accounts.models import SchoolProfile
            prof = SchoolProfile.objects.filter(school_id=self.school_id).values_list("level_id", flat=True).first()
            if prof and self.grade.level_id != prof:
                errors["grade"] = "پایه این کلاس با مقطع مدرسه هم‌خوان نیست."
            if prof and self.study_field_id and self.study_field.level_id and self.study_field.level_id != prof:
                errors["study_field"] = "رشته این کلاس با مقطع مدرسه هم‌خوان نیست."
        t = self.homeroom_teacher if self.homeroom_teacher_id else None
        if t and self.school_id and (t.role != "teacher" or t.org_id != self.school_id):
            errors["homeroom_teacher"] = "معلم باید از کادر همین مدرسه باشد."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.name} — {self.school} ({self.academic_year})"


class SchoolField(models.Model):
    """A field (رشته) that a school offers. The field catalogue is shared; each school gets its own list."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="school_fields")
    field = models.ForeignKey(StudyField, on_delete=models.PROTECT, related_name="schools")

    class Meta:
        verbose_name = "رشته مدرسه"
        constraints = [models.UniqueConstraint(fields=["school", "field"], name="uniq_school_field")]


class GradeWeeklyHours(models.Model):
    """مصوبه‌ی ساعت هفتگی: how many hours a week a lesson has in a grade (and in a field, when it is a field's lesson)."""
    grade = models.ForeignKey(Grade, on_delete=models.CASCADE, related_name="weekly_hours", verbose_name="پایه")
    field = models.ForeignKey(StudyField, null=True, blank=True, on_delete=models.CASCADE, related_name="weekly_hours", verbose_name="رشته")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="approved_hours", verbose_name="درس")
    hours = models.PositiveSmallIntegerField("ساعت در هفته", default=2)

    class Meta:
        verbose_name = "مصوبه‌ی ساعت هفتگی"
        constraints = [
            models.UniqueConstraint(fields=["grade", "field", "subject"], name="uniq_approved_hours"),
            models.UniqueConstraint(fields=["grade", "subject"], condition=models.Q(field__isnull=True), name="uniq_approved_hours_general"),
        ]


class SchoolSubjectSetting(models.Model):
    """سختی درس در یک مدرسه (تعیین‌شده توسط مدیر یا معاون آموزشی). اگر نباشد، سختی پیش‌فرض درس اعمال می‌شود."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="subject_settings", verbose_name="مدرسه")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="school_settings", verbose_name="درس")
    difficulty = models.PositiveSmallIntegerField("سختی درس (۱ آسان تا ۵ سخت)", default=3)

    class Meta:
        verbose_name = "سختی درس در مدرسه"
        constraints = [models.UniqueConstraint(fields=["school", "subject"], name="uniq_school_subject_setting")]

