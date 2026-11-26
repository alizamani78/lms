from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Meeting(models.Model):
    """A meeting of the school: teachers' council, school council, a meeting with parents and educators, or another meeting."""
    KIND = [("teachers_council", "شورای معلمین"), ("school_council", "شورای مدرسه"),
            ("parents_meeting", "جلسه با اولیا و مربیان"), ("other", "جلسه دیگر")]
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="meetings")
    kind = models.CharField(max_length=20, choices=KIND, default="teachers_council")
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True)
    agenda = models.TextField("دستور جلسه", blank=True)
    minutes = models.TextField("صورت‌جلسه (متن ویرایشگر، پاک‌سازی‌شده)", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "جلسه"
        ordering = ["-starts_at", "-id"]

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "پایان جلسه باید بعد از شروع آن باشد."})


class Invitation(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="invitations")
    invitee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="meeting_invitations")
    sent_at = models.DateTimeField(null=True, blank=True)
    attended = models.BooleanField("حاضر بود", null=True, blank=True)

    class Meta:
        verbose_name = "دعوتنامه"
        constraints = [models.UniqueConstraint(fields=["meeting", "invitee"], name="uniq_meeting_invitee")]


class Decision(models.Model):
    """مصوبه: what was approved, who follows it up and until when."""
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="decisions")
    text = models.TextField("متن مصوبه", max_length=2000)
    follow_up = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+",
                                  verbose_name="فرد پیگیری")
    due_date = models.DateField("مهلت", null=True, blank=True)
    done = models.BooleanField("انجام شد", default=False)

    class Meta:
        verbose_name = "مصوبه"
        ordering = ["id"]
