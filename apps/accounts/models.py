from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Value

from apps.core.persian import is_valid_national_id
from django.db.models.functions import Concat, Substr


class OrgUnit(models.Model):
    """Province -> County -> Region -> School tree with a materialised path for fast subtree filters."""

    class Kind(models.TextChoices):
        PROVINCE = "province", "استان"
        COUNTY = "county", "شهرستان"
        REGION = "region", "منطقه"
        SCHOOL = "school", "مدرسه"

    # which kinds of parent each kind may sit under (None = top level)
    ALLOWED_PARENTS = {
        "province": {None},
        "county": {"province"},
        "region": {"county", "province"},
        "school": {"region", "county"},
    }

    name = models.CharField("نام", max_length=150)
    kind = models.CharField("نوع", max_length=10, choices=Kind.choices)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="children", verbose_name="واحد بالاتر")
    code = models.CharField("کد", max_length=20, null=True, blank=True, unique=True)
    path = models.CharField(max_length=255, default="/", editable=False)

    class Meta:
        verbose_name = "واحد سازمانی"
        verbose_name_plural = "همه واحدهای سازمانی"
        indexes = [models.Index(fields=["path"], opclasses=["varchar_pattern_ops"], name="orgunit_path_pattern")]

    def __str__(self):
        return self.name

    def _validate_parent(self):
        parent_kind = self.parent.kind if self.parent_id else None
        if parent_kind not in self.ALLOWED_PARENTS.get(self.kind, set()):
            raise ValidationError({"parent": "واحد بالاتر برای این نوع واحد مجاز نیست."})
        if self.pk and self.parent_id and (self.parent_id == self.pk or self.parent.path.startswith(self.path)):
            raise ValidationError({"parent": "واحد بالاتر نمی‌تواند خود واحد یا زیرمجموعه آن باشد."})

    def clean(self):
        if getattr(self, "KIND", None):
            self.kind = self.KIND
        self._validate_parent()

    def save(self, *args, **kwargs):
        if getattr(self, "KIND", None):
            self.kind = self.KIND
        self._validate_parent()
        old_path = self.path if self.pk else None
        super().save(*args, **kwargs)
        new_path = f"{self.parent.path if self.parent_id else '/'}{self.pk}/"
        if self.path != new_path:
            self.path = new_path
            super().save(update_fields=["path"])
            if old_path and old_path != "/":     # moved: rewrite the whole subtree's paths
                OrgUnit._base_manager.filter(path__startswith=old_path).exclude(pk=self.pk).update(
                    path=Concat(Value(new_path), Substr("path", len(old_path) + 1), output_field=models.CharField()))


def _kind_manager(kind):
    class KindManager(models.Manager):
        def get_queryset(self):
            return super().get_queryset().filter(kind=kind)
    return KindManager


class Province(OrgUnit):
    KIND = "province"
    objects = _kind_manager("province")()

    class Meta:
        proxy = True
        verbose_name = "استان"
        verbose_name_plural = "استان‌ها"


class County(OrgUnit):
    KIND = "county"
    objects = _kind_manager("county")()

    class Meta:
        proxy = True
        verbose_name = "شهرستان"
        verbose_name_plural = "شهرستان‌ها"


class Region(OrgUnit):
    KIND = "region"
    objects = _kind_manager("region")()

    class Meta:
        proxy = True
        verbose_name = "منطقه / ناحیه"
        verbose_name_plural = "مناطق / نواحی"


class School(OrgUnit):
    KIND = "school"
    objects = _kind_manager("school")()

    class Meta:
        proxy = True
        verbose_name = "مدرسه"
        verbose_name_plural = "مدارس"


class User(AbstractUser):
    """`username` is the national code / staff id."""

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "مدیر کل"
        PROVINCE_ADMIN = "province_admin", "مدیر استان"
        COUNTY_ADMIN = "county_admin", "مدیر شهرستان"
        REGION_ADMIN = "region_admin", "مدیر منطقه"
        SCHOOL_ADMIN = "school_admin", "مدیر مدرسه"
        SCHOOL_DEPUTY_EDU = "school_deputy_edu", "معاون آموزشی"
        SCHOOL_DEPUTY_EXEC = "school_deputy_exec", "معاون اجرایی"
        SCHOOL_DEPUTY_CULTURE = "school_deputy_culture", "معاون پرورشی"
        TEACHER = "teacher", "معلم"
        STUDENT = "student", "دانش‌آموز"
        PARENT = "parent", "ولی / سرپرست"
        EXPERT = "expert", "کارشناس"

    role = models.CharField("نقش", max_length=30, choices=Role.choices, default=Role.STUDENT, db_index=True)
    national_id = models.CharField("کد ملی", max_length=10, null=True, blank=True, unique=True)
    student_code = models.CharField("کد دانش‌آموزی", max_length=20, null=True, blank=True, unique=True)
    personnel_code = models.CharField("کد پرسنلی (کادر مدرسه)", max_length=20, blank=True, default="", db_index=True)
    father_name = models.CharField("نام پدر", max_length=100, blank=True)
    mobile = models.CharField("موبایل", max_length=15, blank=True)
    gender = models.CharField("جنسیت", max_length=6, blank=True, choices=[("boy", "پسر"), ("girl", "دختر")])
    expert_kind = models.CharField("نوع کارشناس", max_length=12, blank=True,
                                   choices=[("curriculum", "کارشناس آموزش مقطع"), ("assessment", "کارشناس سنجش منطقه")])
    expert_level = models.ForeignKey("academics.Level", null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="experts", verbose_name="مقطع")
    photo = models.ForeignKey("files.FileAsset", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                              verbose_name="تصویر پروفایل")
    # پایه و رشته‌ی دانش‌آموز (برای تخصیص به کلاس‌ها؛ با افزودن به کلاس تنظیم می‌شود)
    grade = models.ForeignKey("academics.Grade", null=True, blank=True, on_delete=models.SET_NULL, related_name="students_in_grade", verbose_name="پایه")
    study_field = models.ForeignKey("academics.StudyField", null=True, blank=True, on_delete=models.SET_NULL, related_name="students_in_field", verbose_name="رشته")
    org = models.ForeignKey(OrgUnit, null=True, blank=True, on_delete=models.PROTECT,
                            related_name="users", verbose_name="واحد سازمانی")

    ADMIN_ROLES = ("super_admin", "province_admin", "county_admin", "region_admin", "school_admin")
    # school deputies work inside ONE school (or several of one county) like principals, but are not administrators
    SCHOOL_ROLES = ("school_admin", "school_deputy_edu", "school_deputy_exec", "school_deputy_culture", "teacher")
    STAFF_ROLES = ADMIN_ROLES + ("school_deputy_edu", "school_deputy_exec", "school_deputy_culture", "teacher")
    # read-only roles (experts): they see data inside their scope and can change nothing
    READ_ROLES = STAFF_ROLES + ("expert",)
    RANK = {"super_admin": 0, "province_admin": 1, "county_admin": 2, "region_admin": 3,
            "school_admin": 4, "school_deputy_edu": 4.5, "school_deputy_exec": 4.5, "school_deputy_culture": 4.5, "teacher": 5, "expert": 5, "student": 6, "parent": 6}

    session_key = models.CharField("شناسه‌ی نشست فعال", max_length=64, blank=True, default="", editable=False)

    class Meta(AbstractUser.Meta):
        verbose_name = "کاربر"
        verbose_name_plural = "کاربران"

    def clean(self):
        super().clean()
        if self.role == self.Role.STUDENT and self.org_id and self.org.kind == "school":
            from apps.accounts.models import SchoolProfile as _SP
            fixed = {"boys": "boy", "girls": "girl"}.get(_SP.objects.filter(school_id=self.org_id).values_list("gender", flat=True).first(), "")
            if fixed:
                self.gender = fixed                       # single-gender school: the student's gender follows the school
        if self.national_id and not is_valid_national_id(self.national_id):
            raise ValidationError({"national_id": "کد ملی معتبر نیست."})
        # role-dependent fields (the forms show/hide the same ones): level for principals and experts, kind for experts
        if self.role in (self.Role.SCHOOL_ADMIN, self.Role.SCHOOL_DEPUTY_EDU, self.Role.SCHOOL_DEPUTY_EXEC, self.Role.SCHOOL_DEPUTY_CULTURE, self.Role.TEACHER) and self.pk:
            existing = [m.school for m in SchoolMembership.objects.filter(user_id=self.pk).select_related("school")]
            try:
                check_one_county(self, org=self.org if self.org_id else None, membership_schools=existing)
            except ValidationError as e:
                raise ValidationError({"org": e.messages[0]})
        if self.role == self.Role.SCHOOL_ADMIN:
            if self.org_id is None or self.org.kind not in ("school", "region"):
                raise ValidationError({"org": "برای مدیر مدرسه، منطقه (ناحیه) یا مدرسه را انتخاب کنید."})
            if self.expert_level_id is None:
                raise ValidationError({"expert_level": "مقطع مدیر مدرسه را انتخاب کنید (مثلاً متوسطه اول یا دوم)."})
            self.expert_kind = ""
            if self.org.kind == "school":
                from apps.accounts.models import SchoolProfile
                prof = SchoolProfile.objects.filter(school=self.org).first()
                if prof is not None and prof.level_id != self.expert_level_id:
                    raise ValidationError({"expert_level": "مقطع مدیر با مقطع ثبت‌شده برای این مدرسه یکی نیست."})
        elif self.role == self.Role.EXPERT:
            if self.expert_kind not in ("curriculum", "assessment"):
                raise ValidationError({"expert_kind": "نوع کارشناس را انتخاب کنید (آموزش یا سنجش)."})
            if self.org_id is None or self.org.kind not in ("province", "county", "region"):
                raise ValidationError({"org": "کارشناس به استان، شهرستان یا ناحیه/منطقه وصل می‌شود."})
            if self.expert_level_id is None:
                raise ValidationError({"expert_level": "مقطع کارشناس را انتخاب کنید."})
        else:
            self.expert_kind, self.expert_level = "", None

    def save(self, *args, **kwargs):
        # No username is typed by people: everyone signs in with the national code (or student code) and a password.
        # Only administrators keep a username of their own; for the others one is made from their code.
        if not self.username:
            import uuid
            base = self.national_id or self.student_code or uuid.uuid4().hex[:12]
            candidate, n = base, 1
            while User.objects.filter(username=candidate).exclude(pk=self.pk).exists():
                n += 1
                candidate = f"{base}-{n}"
            self.username = candidate
        # `createsuperuser` leaves the default role (student); a superuser is always a super admin.
        if self.is_superuser and self.role == self.Role.STUDENT:
            self.role = self.Role.SUPER_ADMIN
        # Admin-site access follows the role; it is never granted by hand.
        self.is_staff = self.is_superuser or self.role in self.STAFF_ROLES
        super().save(*args, **kwargs)
        # Home school of principals/teachers is also one of their schools.
        if self.org_id and self.role in ("school_admin", "school_deputy_edu", "school_deputy_exec", "school_deputy_culture", "teacher") and self.org.kind == "school":
            SchoolMembership.objects.get_or_create(user=self, school=self.org)


def county_of(org):
    """The county (شهرستان) a unit belongs to, or None when it sits directly under the province."""
    node = org
    while node is not None:
        if node.kind == "county":
            return node.pk
        node = node.parent
    return None


def check_one_county(user, org=None, membership_schools=None):
    """A principal or a teacher works in ONE county only (he may head or teach in several schools of that county).
    Raises ValidationError when the schools would span two counties."""
    schools = list(membership_schools or [])
    if org is not None and org.kind == "school":
        schools.append(org)
    counties = {county_of(s) for s in schools if s is not None}
    counties.discard(None)
    if len(counties) > 1:
        raise ValidationError("مدیر یا معلم نمی‌تواند در بیش از یک شهرستان مدرسه داشته باشد.")


class SchoolMembership(models.Model):
    """A principal (school_admin) or teacher may work in several schools; a student belongs to exactly one (User.org)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="school_memberships", verbose_name="کاربر")
    school = models.ForeignKey(OrgUnit, on_delete=models.CASCADE, related_name="memberships", verbose_name="مدرسه",
                               limit_choices_to={"kind": "school"})
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "عضویت در مدرسه"
        verbose_name_plural = "عضویت کادر در مدارس"
        constraints = [models.UniqueConstraint(fields=["user", "school"], name="uniq_school_membership")]

    def clean(self):
        if self.user_id and self.user.role not in ("school_admin", "school_deputy_edu", "school_deputy_exec", "school_deputy_culture", "teacher"):
            raise ValidationError({"user": "فقط مدیر مدرسه یا معلم می‌تواند به چند مدرسه وصل شود."})
        if self.school_id and self.school.kind != "school":
            raise ValidationError({"school": "فقط مدرسه قابل انتخاب است."})
        if self.user_id and self.school_id:
            others = [m.school for m in SchoolMembership.objects.filter(user_id=self.user_id).exclude(pk=self.pk).select_related("school")]
            if self.user.org_id and self.user.org.kind == "school":
                others.append(self.user.org)
            try:
                check_one_county(self.user, membership_schools=others + [self.school])
            except ValidationError:
                raise ValidationError({"school": "این کاربر قبلاً در شهرستان دیگری مدرسه دارد؛ هر مدیر یا معلم فقط در یک شهرستان کار می‌کند."})


class ParentLink(models.Model):
    """Links a parent/guardian account to a student. Created and verified by school staff only."""
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="child_links",
                               limit_choices_to={"role": "parent"}, verbose_name="ولی")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_links",
                                limit_choices_to={"role": "student"}, verbose_name="دانش‌آموز")
    RELATION_CHOICES = [("father", "پدر"), ("mother", "مادر"), ("grandfather", "پدربزرگ"), ("grandmother", "مادربزرگ"),
                        ("uncle_maternal", "دایی"), ("uncle", "عمو"), ("aunt", "عمه"), ("aunt_maternal", "خاله"), ("other", "سایر")]
    relation = models.CharField("نسبت", max_length=30, blank=True, choices=RELATION_CHOICES)
    is_verified = models.BooleanField("تأییدشده توسط مدرسه", default=False)
    notify_sms = models.BooleanField("پیامک وضعیت فرزند", default=True,
                                     help_text="اگر خاموش باشد، پیامک‌های حضور، آزمون و تکلیف برای این ولی ارسال نمی‌شود.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ارتباط ولی و دانش‌آموز"
        verbose_name_plural = "ارتباط اولیا و دانش‌آموزان"
        constraints = [models.UniqueConstraint(fields=["parent", "student"], name="uniq_parent_student")]

    def clean(self):
        if self.parent_id and self.parent.role != "parent":
            raise ValidationError({"parent": "کاربر انتخاب‌شده نقش «ولی» ندارد."})
        if self.student_id and self.student.role != "student":
            raise ValidationError({"student": "کاربر انتخاب‌شده دانش‌آموز نیست."})


class TOTPDevice(models.Model):
    """Authenticator-app (TOTP) second factor. The secret is stored encrypted (Fernet)."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret_enc = models.TextField(editable=False)
    confirmed = models.BooleanField("تأییدشده", default=False)
    last_step = models.BigIntegerField(default=0, editable=False)      # replay protection
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "دستگاه تأیید دو مرحله‌ای"
        verbose_name_plural = "دستگاه‌های تأیید دو مرحله‌ای"


class SchoolProfile(models.Model):
    """Profile of a school. A school has ONE school code (آموزشگاه) and ONE level (مقطع)."""

    class SchoolType(models.TextChoices):
        NORMAL = "normal", "عادی"
        EXCEPTIONAL = "exceptional", "استثنایی"
        SHAHED = "shahed", "شاهد"
        TIZHOOSHAN = "tizhooshan", "تیزهوشان"
        NEMOONE = "nemoone", "نمونه دولتی"

    class Shift(models.TextChoices):
        DAILY = "daily", "روزانه"
        NIGHT = "night", "شبانه"

    class Ownership(models.TextChoices):
        PUBLIC = "public", "دولتی"
        NONPUBLIC = "nonpublic", "غیردولتی"
        VIRTUAL = "virtual", "مجازی"
        INSTITUTE = "institute", "موسسه آموزشی"

    class Gender(models.TextChoices):
        BOYS = "boys", "پسرانه"
        GIRLS = "girls", "دخترانه"
        MIXED = "mixed", "مختلط"

    school = models.OneToOneField(OrgUnit, on_delete=models.CASCADE, related_name="profile",
                                  limit_choices_to={"kind": "school"}, verbose_name="مدرسه")
    school_code = models.CharField("کد آموزشگاه", max_length=20, unique=True)
    level = models.ForeignKey("academics.Level", on_delete=models.PROTECT, related_name="schools", verbose_name="مقطع")
    branch = models.CharField("شاخه", max_length=40, blank=True, default="")
    school_type = models.CharField("نوع", max_length=12, choices=SchoolType.choices, default=SchoolType.NORMAL)
    shift = models.CharField("شیفت", max_length=6, choices=Shift.choices, default=Shift.DAILY)
    ownership = models.CharField("مالکیت", max_length=10, choices=Ownership.choices, default=Ownership.PUBLIC)
    gender = models.CharField("جنسیت مدرسه", max_length=6, choices=Gender.choices, default=Gender.MIXED)
    country = models.CharField("کشور", max_length=60, default="ایران")
    county = models.ForeignKey(OrgUnit, null=True, blank=True, on_delete=models.PROTECT, related_name="school_profiles",
                               limit_choices_to={"kind": "county"}, verbose_name="شهرستان (نشانی)")
    postal_code = models.CharField("کد پستی", max_length=10, blank=True)
    address = models.CharField("نشانی", max_length=300, blank=True)
    phone = models.CharField("تلفن", max_length=20, blank=True)
    fax = models.CharField("فکس", max_length=20, blank=True)
    website = models.URLField("وب‌سایت", max_length=200, blank=True)
    email = models.EmailField("ایمیل", blank=True)

    exam_support = models.CharField("شماره/آیدی پشتیبانی آزمون", max_length=200, blank=True, default="")
    exam_guide = models.TextField("راهنمای پیش‌فرض آزمون‌های این مدرسه (خالی = متن استاندارد)", blank=True, default="")
    class Meta:
        verbose_name = "مشخصات مدرسه"
        verbose_name_plural = "مشخصات مدارس"

    def __str__(self):
        return f"{self.school.name} ({self.school_code})"

    def clean(self):
        from django.core.exceptions import ValidationError as VE
        if self.school_id and self.school.kind != "school":
            raise VE({"school": "فقط مدرسه قابل انتخاب است."})
        if self.school_code and not self.school_code.isalnum():
            raise VE({"school_code": "کد آموزشگاه فقط حروف و عدد باشد."})


def school_gender_for(profile):
    """A boys-only or girls-only school fixes the gender of its students; a mixed school leaves it to the entry."""
    if profile is None:
        return ""
    return {"boys": "boy", "girls": "girl"}.get(profile.gender, "")


def gender_allowed(gender, profile):
    """Girls may not join boys-only schools and vice-versa; boy/girl rules apply to upper secondary, vocational
    and technical levels only. Primary and lower-secondary schools may be mixed."""
    if profile is None or not gender:
        return True
    strict_levels = {"MS2", "KAR", "FNI"}
    if profile.level.code not in strict_levels:
        return True
    if profile.gender == SchoolProfile.Gender.BOYS:
        return gender == "boy"
    if profile.gender == SchoolProfile.Gender.GIRLS:
        return gender == "girl"
    return True


# The username may be left empty (it is then made automatically on save); the field stays unique.
User._meta.get_field("username").blank = True


class RoleAssignment(models.Model):
    """An additional role of a person (the main role stays on User.role). A school role names its school:
    a deputy in one school and a teacher in another, or a principal who is also a teacher in the same school."""
    SCHOOL_ROLE_NAMES = ("school_admin", "school_deputy_edu", "school_deputy_exec", "school_deputy_culture", "teacher")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="extra_roles", verbose_name="کاربر")
    role = models.CharField("نقش", max_length=30, choices=User.Role.choices)
    school = models.ForeignKey("OrgUnit", null=True, blank=True, on_delete=models.CASCADE, related_name="role_assignments",
                               limit_choices_to={"kind": "school"}, verbose_name="مدرسه")

    class Meta:
        verbose_name = "نقش اضافی"
        constraints = [models.UniqueConstraint(fields=["user", "role", "school"], name="uniq_role_assignment")]

    def clean(self):
        if self.role in self.SCHOOL_ROLE_NAMES and not self.school_id:
            raise ValidationError({"school": "برای این نقش مدرسه را انتخاب کنید."})
        if self.role not in self.SCHOOL_ROLE_NAMES and self.school_id:
            raise ValidationError({"school": "این نقش به مدرسه وصل نمی‌شود."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.school_id and self.role in self.SCHOOL_ROLE_NAMES:
            SchoolMembership.objects.get_or_create(user=self.user, school=self.school)
