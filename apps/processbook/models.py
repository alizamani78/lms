from django.conf import settings
from django.db import models
from django.utils import timezone

FIELD_TYPES = ("text", "number", "date", "bool", "choice")


class ProcessDefinition(models.Model):
    """A process of the process book (فرایند), defined by the educational deputy for his school. Its fields are the
    questions asked each time (type: text, number, date, bool, choice). Sensitive processes (health) are visible only to
    the educational deputy, the principal and the person who recorded the entry."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="process_definitions")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sensitive = models.BooleanField("اطلاعات حساس (سلامت)", default=False)
    fields = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "فرایند"
        ordering = ["title"]
        constraints = [models.UniqueConstraint(fields=["school", "title"], name="uniq_process_title_per_school")]

    def __str__(self):
        return self.title


class ProcessEntry(models.Model):
    definition = models.ForeignKey(ProcessDefinition, on_delete=models.CASCADE, related_name="entries")
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="process_entries")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="process_entries")
    date = models.DateField(default=timezone.localdate)
    values = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=500, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ثبت فرایند"
        ordering = ["-date", "-id"]


class TeacherEvaluation(models.Model):
    """ارزشیابی معلم by the educational deputy: each criterion scored 1 to 5, the average is kept."""
    CRITERIA = [("teaching", "کیفیت تدریس"), ("class_management", "مدیریت کلاس"), ("interaction", "تعامل با دانش‌آموزان"),
                ("punctuality", "پایبندی به برنامه و حضور"), ("development", "به‌روزرسانی علمی و آموزشی")]
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="teacher_evaluations")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="evaluations_received")
    evaluator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    date = models.DateField(default=timezone.localdate)
    scores = models.JSONField(default=dict)
    average = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ارزشیابی معلم"
        ordering = ["-date", "-id"]
