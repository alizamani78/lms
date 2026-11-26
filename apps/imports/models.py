from django.conf import settings
from django.db import models


class ImportJob(models.Model):
    """One bulk import session: the parsed rows are kept (preview) until committed. Only valid rows are written."""

    class Kind(models.TextChoices):
        STUDENTS = "students", "دانش‌آموزان"
        CLASSES = "classes", "کلاس‌ها"
        TEACHERS = "teachers", "دبیران"
        COURSES = "courses", "درس‌های کلاس"
        SUBJECTS = "subjects", "سرفصل درس‌ها"
        CLASS_TEACHERS = "class_teachers", "معلم و درس به تفکیک کلاس (سیدا)"
        STAFF = "staff", "کادر مدرسه (سیدا / CSV)"
        FIELDS = "fields", "رشته‌های مدرسه (سیدا / CSV)"
        CURRICULUM = "curriculum", "دروس پایه و رشته (سیدا / CSV)"

    class Status(models.TextChoices):
        PREVIEW = "preview", "پیش‌نمایش"
        COMMITTED = "committed", "ثبت‌شده"

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="import_jobs")
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="import_jobs",
                               limit_choices_to={"kind": "school"})
    kind = models.CharField(max_length=20, choices=Kind.choices)
    source_name = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    rows = models.JSONField(default=list)
    unknown_headers = models.JSONField(default=list)
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "ورود دسته‌ای"
        verbose_name_plural = "ورودهای دسته‌ای"
        ordering = ["-created_at"]
