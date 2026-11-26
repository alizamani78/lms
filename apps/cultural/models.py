from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Competition(models.Model):
    """A competition or festival inside the school (مسابقه یا جشنواره درون‌مدرسه‌ای)."""
    KIND = [("competition", "مسابقه"), ("festival", "جشنواره")]
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="competitions")
    kind = models.CharField(max_length=12, choices=KIND, default="competition")
    title = models.CharField(max_length=200)
    date = models.DateField()
    description = models.TextField(blank=True)
    is_published = models.BooleanField("نمایش نتایج به دانش‌آموزان", default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "مسابقه یا جشنواره"
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.title


class CompetitionResult(models.Model):
    """A top place (رتبه ۱ تا ۵). The score is entered by hand by the cultural deputy (امتیاز دستی)."""
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="results")
    rank = models.PositiveSmallIntegerField()
    student = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField("نام (برای گروه یا شخص خارج از فهرست)", max_length=200, blank=True)
    score = models.DecimalField("امتیاز", max_digits=7, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "نفر برتر"
        ordering = ["rank"]
        constraints = [models.UniqueConstraint(fields=["competition", "rank"], name="uniq_competition_rank")]

    def clean(self):
        if not (1 <= self.rank <= 5):
            raise ValidationError({"rank": "رتبه باید بین ۱ تا ۵ باشد."})


class Trip(models.Model):
    """An excursion (اردو) with its participants and their consent forms (رضایت‌نامه)."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="trips")
    TRIP_TYPES = [("excursion", "اردو"), ("science_visit", "بازدید علمی"), ("cultural", "فرهنگی"), ("tourism", "سیاحتی"),
                  ("pilgrimage", "زیارتی"), ("basij", "بسیج"), ("sport", "ورزشی"), ("rahian", "راهیان نور")]
    trip_type = models.CharField("نوع اردو", max_length=20, choices=TRIP_TYPES, default="excursion", blank=True)
    departure_at = models.DateTimeField("تاریخ و ساعت اعزام", null=True, blank=True)
    departure_place = models.CharField("محل شروع اعزام", max_length=200, blank=True, default="")
    duration_days = models.PositiveSmallIntegerField("مدت (روز)", null=True, blank=True)
    consent_deadline = models.DateField("مهلت تحویل رضایت‌نامه", null=True, blank=True)
    title = models.CharField(max_length=200)
    destination = models.CharField(max_length=200, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "اردو"
        ordering = ["-start_date", "-id"]

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError({"end_date": "پایان اردو نمی‌تواند قبل از شروع باشد."})


class TripParticipant(models.Model):
    CONSENT = [("pending", "در انتظار"), ("accepted", "رضایت داده"), ("declined", "رضایت نداده")]
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="participants")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    consent = models.CharField(max_length=10, choices=CONSENT, default="pending")
    guardian_name = models.CharField("نام ولی که رضایت داده", max_length=150, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "شرکت‌کننده اردو"
        constraints = [models.UniqueConstraint(fields=["trip", "student"], name="uniq_trip_student")]


class CompetitionParticipant(models.Model):
    """A student taking part in a competition or festival, and the guardian's consent (رضایت‌نامه جشنواره)."""
    CONSENT = [("pending", "در انتظار"), ("accepted", "رضایت داده"), ("declined", "رضایت نداده")]
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="participants")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    consent = models.CharField(max_length=10, choices=CONSENT, default="pending")
    guardian_name = models.CharField(max_length=150, blank=True)
    consent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "شرکت‌کننده جشنواره"
        constraints = [models.UniqueConstraint(fields=["competition", "student"], name="uniq_competition_student")]
