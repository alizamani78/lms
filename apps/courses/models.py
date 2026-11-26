from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Course(models.Model):
    """A subject taught in a school (optionally tied to a curriculum subject and a class)."""
    org = models.ForeignKey("accounts.OrgUnit", on_delete=models.PROTECT, related_name="courses",
                            limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    title = models.CharField("عنوان درس", max_length=200)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="teaching",
                                limit_choices_to={"role": "teacher"}, verbose_name="معلم", null=True, blank=True)
    subject = models.ForeignKey("academics.Subject", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="courses", verbose_name="درس برنامه (سرفصل)")
    classroom = models.ForeignKey("academics.ClassRoom", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="courses", verbose_name="کلاس")
    academic_year = models.ForeignKey("academics.AcademicYear", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="courses", verbose_name="سال تحصیلی")

    class Meta:
        verbose_name = "درس"
        verbose_name_plural = "درس‌ها"
        indexes = [models.Index(fields=["org"]), models.Index(fields=["teacher"])]

    def __str__(self):
        return self.title

    def clean(self):
        errors = {}
        if self.org_id and self.org.kind != "school":
            errors["org"] = "فقط مدرسه قابل انتخاب است."
        if self.teacher_id and self.org_id:
            from apps.accounts.models import SchoolMembership
            member = self.teacher.org_id == self.org_id or SchoolMembership.objects.filter(
                user_id=self.teacher_id, school_id=self.org_id).exists()
            if self.teacher.role != "teacher" or not member:
                errors["teacher"] = "معلم باید عضو کادر همین مدرسه باشد."
        if self.classroom_id and self.org_id and self.classroom.school_id != self.org_id:
            errors["classroom"] = "کلاس باید متعلق به همین مدرسه باشد."
        if self.subject_id and self.classroom_id and not self.subject.grades.filter(pk=self.classroom.grade_id).exists():
            errors["subject"] = "این درس برای پایه کلاس تعریف نشده است."
        if self.classroom_id and self.academic_year_id and self.classroom.academic_year_id != self.academic_year_id:
            errors["academic_year"] = "سال تحصیلی با سال تحصیلی کلاس یکی نیست."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.academic_year_id and self.classroom_id:
            self.academic_year_id = self.classroom.academic_year_id
        super().save(*args, **kwargs)


class Enrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments", verbose_name="درس")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments",
                                limit_choices_to={"role": "student"}, verbose_name="دانش‌آموز")
    academic_year = models.ForeignKey("academics.AcademicYear", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="enrollments", verbose_name="سال تحصیلی")

    class Meta:
        verbose_name = "ثبت‌نام در درس"
        verbose_name_plural = "ثبت‌نام‌های درس"
        constraints = [models.UniqueConstraint(fields=["course", "student"], name="uniq_enrollment")]
        indexes = [models.Index(fields=["student", "course"])]

    def save(self, *args, **kwargs):
        if not self.academic_year_id and self.course_id:
            self.academic_year_id = self.course.academic_year_id       # every enrolment belongs to a school year
        super().save(*args, **kwargs)

    def clean(self):
        if self.course_id and self.student_id and (self.student.role != "student"
                                                   or self.student.org_id != self.course.org_id):
            raise ValidationError({"student": "دانش‌آموز باید از همین مدرسه باشد."})


class ContentItem(models.Model):
    """Learning material. Files/videos live in object storage/CDN or a media server: we store only the URL."""

    class Kind(models.TextChoices):
        VIDEO = "video", "ویدئو"
        AUDIO = "audio", "صوت"
        IMAGE = "image", "عکس"
        DOCUMENT = "document", "سند (PDF / Word)"
        PRESENTATION = "presentation", "پاورپوینت"
        SPREADSHEET = "spreadsheet", "اکسل"
        ARCHIVE = "archive", "فایل فشرده (zip / rar)"
        TEXT = "text", "متن (با ویرایشگر، عکس و صوت)"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="content_items", verbose_name="درس")
    title = models.CharField("عنوان", max_length=200)
    kind = models.CharField("نوع", max_length=14, choices=Kind.choices, default=Kind.DOCUMENT)
    url = models.CharField("آدرس (اختیاری؛ استفاده نمی‌شود)", max_length=500, blank=True, editable=False)
    file = models.ForeignKey("files.FileAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                             verbose_name="فایل بارگذاری‌شده")
    body = models.TextField("متن", blank=True)
    embedded_files = models.ManyToManyField("files.FileAsset", blank=True, related_name="+", editable=False,
                                            verbose_name="عکس و صوت داخل متن")
    order = models.PositiveSmallIntegerField("ترتیب", default=0)
    is_published = models.BooleanField("منتشرشده", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "محتوای درسی"
        verbose_name_plural = "محتوای درسی"
        ordering = ["order", "id"]
        indexes = [models.Index(fields=["course", "is_published", "order"])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.kind == self.Kind.TEXT:
            if not (self.body or "").strip():
                raise ValidationError({"body": "برای محتوای متنی، متن لازم است."})
        elif not self.file_id:
            raise ValidationError({"file": "برای این نوع محتوا، فایل را بارگذاری کنید."})
        if len(self.body or "") > 200_000:
            raise ValidationError({"body": "متن بیش از حد طولانی است."})

    def save(self, *args, **kwargs):
        """Rich text is sanitised on EVERY save, whoever wrote it (teacher UI, system panel, import)."""
        from apps.core.htmlclean import clean_html
        self.body, ids = clean_html(self.body)
        super().save(*args, **kwargs)
        from apps.files.models import FileAsset
        qs = FileAsset.objects.filter(pk__in=ids, kind__in=["image", "audio"])
        if self.created_by_id:
            qs = qs.filter(owner_id=self.created_by_id)
        self.embedded_files.set(qs)
