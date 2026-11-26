from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class QuestionKind(models.TextChoices):
    MCQ = "mcq", "چندگزینه‌ای (تستی)"
    TRUE_FALSE = "tf", "صحیح / غلط"
    ESSAY = "essay", "تشریحی"
    MULTI = "multi", "چند پاسخ صحیح (هر گزینه درست یا غلط)"
    SHORT = "short", "پاسخ کوتاه"
    MATCH = "match", "جور کردنی"
    DRAG = "drag", "کشیدنی و رها کردنی (قرار دادن در جای درست)"
    FILL = "fill", "تکمیل جای خالی (هر جای خالی نمره‌ی مشخص دارد)"


class Exam(models.Model):
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="exams")
    title = models.CharField(max_length=200)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    is_published = models.BooleanField(default=False)
    is_suspended = models.BooleanField("آزمون به حالت تعلیق", default=False,
                                       help_text="در حالت تعلیق دانش‌آموز نمی‌تواند شروع یا ادامه دهد؛ زمان‌ها حفظ می‌شوند.")
    instructions = models.TextField("توضیحات قبل از شروع (برای دانش‌آموز)", blank=True)
    RESULT_MODES = [("hidden", "نمایش ندارد"), ("score", "فقط نمره کل"), ("counts", "تعداد پاسخ‌های صحیح و غلط"),
                    ("both", "نمره کل و تعداد صحیح و غلط")]
    result_mode = models.CharField("نمایش نتیجه به دانش‌آموز پس از ثبت", max_length=8, choices=RESULT_MODES, default="hidden")
    show_results = models.BooleanField(default=False)   # legacy: True means «فقط نمره کل» when result_mode is hidden
    COMBINE_CHOICES = [("manual", "فقط نمره‌ی دستی"), ("online", "فقط آزمون مجازی"), ("max", "بیشترین نمره"),
                       ("avg", "میانگین"), ("min", "کمترین نمره")]
    combine_mode = models.CharField("ترکیب نمره‌ی دستی و آزمون مجازی", max_length=10, choices=COMBINE_CHOICES, default="manual")
    monthly_percent = models.PositiveSmallIntegerField("درصد نمره‌ی ماهیانه در مستمر (خالی = برابر)", null=True, blank=True)
    assessment = models.CharField("نوع ارزشیابی", max_length=10, default="continuous",
                                  choices=[("continuous", "مستمر"), ("classroom", "سرکلاسی"), ("final", "پایانی")],
                                  help_text="نمره مستمر (ضمن سال) یا نمره پایانی.")
    shuffle_questions = models.BooleanField("ترتیب تصادفی سؤال‌ها", default=False)
    shuffle_choices = models.BooleanField("ترتیب تصادفی گزینه‌ها", default=False)
    draw_count = models.PositiveSmallIntegerField("تعداد سؤال تصادفی برای هر دانش‌آموز", null=True, blank=True)
    allow_back = models.BooleanField("امکان بازگشت به سؤال‌های قبلی", default=True)
    back_penalty = models.DecimalField("نمره‌ی منفی هر بازگشت به عقب", max_digits=5, decimal_places=2, default=Decimal("0"))
    questions_per_screen = models.PositiveSmallIntegerField("تعداد سؤال در هر نمایش (۰ = همه با هم)", default=0)
    updated_at = models.DateTimeField(auto_now=True)      # part of the question-cache key

    class Meta:
        verbose_name = "آزمون"
        verbose_name_plural = "آزمون‌ها"
        indexes = [models.Index(fields=["course", "starts_at"])]

    def __str__(self):
        return self.title

    def clean(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "پایان باید بعد از شروع باشد."})
        if not 1 <= (self.duration_minutes or 0) <= 600:
            raise ValidationError({"duration_minutes": "مدت آزمون باید بین ۱ تا ۶۰۰ دقیقه باشد."})


class Difficulty(models.TextChoices):
    EASY = "easy", "آسان"
    MEDIUM = "medium", "متوسط"
    HARD = "hard", "سخت"


class ExamSection(models.Model):
    """بخش آزمون: a group of questions of one exam with its own title and instructions (the questions may be mixed kinds)."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="sections")
    title = models.CharField("عنوان بخش", max_length=200)
    instructions = models.TextField("متن راهنمای بخش", blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "بخش آزمون"
        ordering = ["order", "id"]


class Question(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    section = models.ForeignKey(ExamSection, null=True, blank=True, on_delete=models.SET_NULL, related_name="questions", verbose_name="بخش")
    kind = models.CharField(max_length=5, choices=QuestionKind.choices, default=QuestionKind.MCQ)
    difficulty = models.CharField("سختی سؤال", max_length=6, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    text = models.TextField()
    points = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1"))
    order = models.PositiveSmallIntegerField(default=0)
    bank_source = models.ForeignKey("exams.BankQuestion", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)

    class Meta:
        verbose_name = "سؤال"
        verbose_name_plural = "سؤال‌ها"
        ordering = ["order", "id"]
        indexes = [models.Index(fields=["exam", "order"])]

    def __str__(self):
        return self.text[:60]


class Choice(models.Model):
    """An option. mcq/tf/multi: text + is_correct. short: accepted answers (is_correct). match/drag: text = left item
    and pair_text = its right side / the slot it belongs to (never sent to students)."""
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.TextField(max_length=4000)              # rich text (sanitised on save)
    pair_text = models.CharField(max_length=500, blank=True)
    is_correct = models.BooleanField(default=False)      # never serialised for students
    weight = models.DecimalField("درصد نمره سؤال", max_digits=5, decimal_places=2, default=Decimal("0"))  # % of the question's points

    class Meta:
        verbose_name = "گزینه"
        verbose_name_plural = "گزینه‌ها"


class Attempt(models.Model):
    back_moves = models.PositiveIntegerField("تعداد بازگشت به عقب", default=0)

    class Grading(models.TextChoices):
        OPEN = "open", "در حال انجام"
        PENDING = "pending", "در انتظار تصحیح تشریحی"
        GRADED = "graded", "تصحیح‌شده"

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attempts")
    started_at = models.DateTimeField(auto_now_add=True)
    deadline = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    question_ids = models.JSONField(default=list, blank=True)          # the questions drawn for this student
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    grading_status = models.CharField(max_length=8, choices=Grading.choices, default=Grading.OPEN)
    adjustment = models.DecimalField(max_digits=7, decimal_places=2, default=0)   # set only by an accepted appeal

    class Meta:
        verbose_name = "شرکت در آزمون"
        verbose_name_plural = "شرکت‌ها در آزمون (نتایج)"
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="one_attempt_per_student")]
        indexes = [models.Index(fields=["submitted_at", "deadline"])]


class Answer(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="+")
    choice = models.ForeignKey(Choice, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    choice_ids = models.JSONField(default=list, blank=True)              # multi: the options the student ticked
    mapping = models.JSONField(default=dict, blank=True)                 # match/drag: {choice id: chosen side}
    text = models.TextField(blank=True)                                  # essay / short answers
    answered_at = models.DateTimeField()
    points_awarded = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)   # essays
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["attempt", "question"], name="one_answer_per_question")]


class BankQuestion(models.Model):
    """Reusable question owned by an org unit (school or any ancestor level). Copied into exams, never shared live."""
    org = models.ForeignKey("accounts.OrgUnit", on_delete=models.PROTECT, related_name="bank_questions",
                            verbose_name="مالک (واحد سازمانی)")
    subject = models.ForeignKey("academics.Subject", null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+", verbose_name="درس")
    grade = models.ForeignKey("academics.Grade", null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+", verbose_name="پایه")
    kind = models.CharField("نوع", max_length=5, choices=QuestionKind.choices, default=QuestionKind.MCQ)
    text = models.TextField("متن سؤال")
    points = models.DecimalField("بارم", max_digits=5, decimal_places=2, default=Decimal("1"))
    is_active = models.BooleanField("فعال", default=True)
    chapter = models.CharField("سرفصل", max_length=200, blank=True, default="")
    difficulty = models.CharField("درجه‌ی سختی", max_length=6, choices=Difficulty.choices, default=Difficulty.MEDIUM)
    SCOPE_CHOICES = [("private", "فقط خودم"), ("school", "مدرسه"), ("region", "منطقه"), ("province", "استان")]
    scope = models.CharField("سطح دسترسی", max_length=10, choices=SCOPE_CHOICES, default="private")
    share_org = models.ForeignKey("accounts.OrgUnit", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                  verbose_name="واحد اشتراک (مدرسه، منطقه یا استان)")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "سؤال بانک"
        verbose_name_plural = "بانک سؤال"
        indexes = [models.Index(fields=["org", "kind"])]

    def __str__(self):
        return self.text[:60]


class BankChoice(models.Model):
    question = models.ForeignKey(BankQuestion, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)


class ExamSeat(models.Model):
    """شماره‌ی داوطلبی و جایگاه یک دانش‌آموز در یک آزمون (for the entry card)."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="seats", verbose_name="آزمون")
    student = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="exam_seats", verbose_name="دانش‌آموز")
    number = models.PositiveIntegerField("شماره‌ی داوطلبی")
    room = models.CharField("جایگاه / کلاس امتحان", max_length=60, blank=True, default="")

    class Meta:
        verbose_name = "شماره‌ی داوطلبی"
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="uniq_exam_seat_student"),
                       models.UniqueConstraint(fields=["exam", "number"], name="uniq_exam_seat_number")]


class ExamHeaderLayout(models.Model):
    """سربرگ برگه‌ی امتحان یک مدرسه: فیلدهای انتخاب‌شده، ردیف و ترتیب و تراز هر فیلد، و جمله‌ی قصار."""
    school = models.OneToOneField("accounts.OrgUnit", on_delete=models.CASCADE, related_name="exam_header", verbose_name="مدرسه")
    layout = models.JSONField("ساختار سربرگ", default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "سربرگ برگه‌ی امتحان"


class ExamSchedule(models.Model):
    """برنامه‌ی امتحانی مدرسه: پیش‌نویس ← در انتظار بررسی ← تأییدشده (یا بایگانی)."""
    DRAFT, REVIEW, APPROVED, ARCHIVED = "draft", "review", "approved", "archived"
    STATUS = [(DRAFT, "پیش‌نویس"), (REVIEW, "در انتظار تأیید"), (APPROVED, "تأییدشده"), (ARCHIVED, "بایگانی")]
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="exam_schedules", verbose_name="مدرسه")
    title = models.CharField("عنوان برنامه‌ی امتحانی", max_length=200)
    status = models.CharField(max_length=10, choices=STATUS, default=DRAFT)
    start_date = models.DateField("از تاریخ")
    end_date = models.DateField("تا تاریخ")
    printed_at = models.DateTimeField("زمان چاپ برنامه", null=True, blank=True)
    shifts = models.JSONField("نوبت‌های امتحان (ساعت شروع و مدت هر نوبت)", default=list, blank=True)
    notes = models.TextField("تذکرات و توضیحات جلسات امتحان (روی کارت ورود چاپ می‌شود)", blank=True, default="")
    invigilators_per_day = models.PositiveSmallIntegerField("تعداد مراقب مورد نیاز در هر روز امتحان", default=2)
    duty_notes = models.TextField("تذکرات مهم مراقبت (برای همه‌ی مراقبان روی کارت چاپ می‌شود)", blank=True, default="")
    hours_per_duty = models.PositiveSmallIntegerField("هر چند ساعت تدریس، یک روز مراقبت", default=6)
    off_days = models.CharField("روزهای تعطیل هفته (شماره‌ی پایتون: ۳ پنجشنبه، ۴ جمعه)", max_length=20, default="4")
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "برنامه‌ی امتحانی"
        ordering = ["-created_at"]


class ExamVenue(models.Model):
    """محل برگزاری امتحان مدرسه (نام و نشانی)، که در کارت ورود به جلسه چاپ می‌شود."""
    school = models.ForeignKey("accounts.OrgUnit", on_delete=models.CASCADE, related_name="exam_venues", verbose_name="مدرسه")
    name = models.CharField("نام محل برگزاری", max_length=150)
    address = models.TextField("نشانی", blank=True, default="")

    class Meta:
        verbose_name = "محل برگزاری امتحان"


class ExamScheduleEntry(models.Model):
    schedule = models.ForeignKey(ExamSchedule, on_delete=models.CASCADE, related_name="entries", verbose_name="برنامه")
    classroom = models.ForeignKey("academics.ClassRoom", on_delete=models.CASCADE, related_name="exam_schedule_entries", verbose_name="کلاس")
    subject = models.ForeignKey("academics.Subject", on_delete=models.PROTECT, related_name="exam_schedule_entries", verbose_name="درس")
    exam = models.ForeignKey(Exam, null=True, blank=True, on_delete=models.SET_NULL, related_name="schedule_entries", verbose_name="آزمون")
    date = models.DateField("تاریخ امتحان")
    start_time = models.TimeField("ساعت شروع")
    duration_minutes = models.PositiveSmallIntegerField("مدت (دقیقه)", default=90)
    venue_name = models.CharField("محل برگزاری", max_length=150, blank=True, default="")
    venue_address = models.TextField("نشانی محل برگزاری", blank=True, default="")

    class Meta:
        verbose_name = "امتحان برنامه"
        constraints = [models.UniqueConstraint(fields=["schedule", "classroom", "date"], name="uniq_exam_class_day"),
                       models.UniqueConstraint(fields=["schedule", "classroom", "subject"], name="uniq_exam_class_subject")]


class ExamDuty(models.Model):
    """مراقبت از امتحان: معلمی که در یک روز امتحان، مراقب است (فقط برای مشاهده‌ی معلمان)."""
    schedule = models.ForeignKey(ExamSchedule, on_delete=models.CASCADE, related_name="duties", verbose_name="برنامه")
    date = models.DateField("تاریخ")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exam_duties", verbose_name="مراقب")
    by_subject = models.BooleanField("معلم درس همان روز است", default=False)
    note = models.TextField("توضیحات برای مراقب (روی کارت چاپ می‌شود)", blank=True, default="")

    class Meta:
        verbose_name = "مراقبت امتحان"
        ordering = ["date", "id"]
        constraints = [models.UniqueConstraint(fields=["schedule", "date", "teacher"], name="uniq_exam_duty_teacher_day")]


class ExamManualScore(models.Model):
    """نمره‌ی دستی امتحان (بدون آزمون مجازی) برای هر دانش‌آموز؛ ترکیب آن با آزمون مجازی طبق ترکیب آزمون انجام می‌شود."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="manual_scores", verbose_name="آزمون")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="manual_exam_scores", verbose_name="دانش‌آموز")
    score = models.DecimalField("نمره (از ۲۰)", max_digits=5, decimal_places=2)

    class Meta:
        verbose_name = "نمره‌ی دستی امتحان"
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="uniq_exam_manual_score")]



class ExamScoreStatus(models.Model):
    """وضعیت نمره‌ی یک دانش‌آموز در یک آزمون: موقت ← اعتراض ← بررسی‌شده ← تأییدشده (نهایی، قفل).
    فقط دبیر درس نمره را تغییر می‌دهد؛ مدیر مدرسه می‌تواند نمره‌ی تأییدشده را باز کند تا دبیر اصلاح کند."""
    TEMPORARY, APPEALED, REVIEWED, FINAL, UNLOCKED = "temporary", "appealed", "reviewed", "final", "unlocked"
    STATUS_CHOICES = [(TEMPORARY, "موقت"), (APPEALED, "اعتراض دارد"), (REVIEWED, "بررسی‌شده (در انتظار تأیید)"),
                      (FINAL, "نهایی (قفل)"), (UNLOCKED, "باز برای اصلاح دبیر")]
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="score_statuses", verbose_name="آزمون")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="exam_score_statuses", verbose_name="دانش‌آموز")
    status = models.CharField("وضعیت", max_length=12, choices=STATUS_CHOICES, default=TEMPORARY)
    appeal_text = models.TextField("متن اعتراض", blank=True, default="")
    appeal_at = models.DateTimeField("زمان اعتراض", null=True, blank=True)
    teacher_note = models.TextField("نظر دبیر پس از بررسی", blank=True, default="")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", verbose_name="تأیید‌کننده")
    approved_at = models.DateTimeField("زمان تأیید", null=True, blank=True)
    unlock_reason = models.CharField("دلیل بازکردن قفل", max_length=300, blank=True, default="")

    class Meta:
        verbose_name = "وضعیت نمره‌ی امتحان"
        constraints = [models.UniqueConstraint(fields=["exam", "student"], name="uniq_exam_score_status")]
