"""Whitelisted resources of the system administrator panel. Only fields listed here can be written by the API;
secrets (passwords, keys) and internal flags are never exposed. Resources marked readonly are shown, never changed."""
from apps.academics.models import AcademicYear, ClassRoom, Grade, Level, StudyField, Subject
from apps.accounts.models import OrgUnit, ParentLink, SchoolMembership, SchoolProfile, TOTPDevice, User
from apps.appeals.models import GradeAppeal
from apps.attendance.models import AttendanceRecord, TeacherAttendance
from apps.core.models import AuditLog, GradeChange
from apps.coursework.models import Assignment, Submission
from apps.courses.models import ContentItem, Course, Enrollment
from apps.exams.models import Attempt, Exam
from apps.files.models import FileAsset
from apps.imports.models import ImportJob
from apps.integrations.models import ExternalSystem
from apps.liveclass.models import ClassSession, ProviderEvent
from apps.reports.models import DailyOrgStats
from apps.schoolcal.models import CalendarEvent
from apps.sms.models import SmsGateway, SmsLog
from apps.timetable.models import BellPeriod, ScheduleEntry
from apps.transfers.models import StudentSchoolHistory, TransferRequest

def _year_name(obj):
    """Academic year name from its two dates (Gregorian input): 1405-1406."""
    import datetime as _dt
    from apps.core.jalali import gregorian_to_jalali
    def as_date(v):
        return _dt.date.fromisoformat(v) if isinstance(v, str) else v
    start, end = as_date(obj.starts_on), as_date(obj.ends_on)
    if start and end and not obj.name:
        y1 = gregorian_to_jalali(start.year, start.month, start.day)[0]
        y2 = gregorian_to_jalali(end.year, end.month, end.day)[0]
        obj.name = f"{y1}-{y2}" if y1 != y2 else f"{y1}-{y1 + 1}"


RESOURCES = {
    "users": {"label": "کاربران", "section": "کاربران و دسترسی", "model": User,
              "fields": ["username", "first_name", "last_name", "national_id", "student_code", "personnel_code", "father_name", "mobile",
                         "gender", "role", "org", "expert_kind", "expert_level", "is_active"],
              "list": ["username", "first_name", "last_name", "role", "org", "is_active"],
              "search": ["username", "first_name", "last_name", "national_id", "student_code"],
              "qs": lambda: User.objects.select_related("org"), "readonly_edit": ["username"]},
    "memberships": {"label": "عضویت کادر در مدارس", "section": "کاربران و دسترسی", "model": SchoolMembership,
                    "fields": ["user", "school"], "list": ["user", "national_code", "school"],
                    # ستون مجازی: از کاربر خوانده می‌شود و فقط برای نمایش است
                    "virtual": {"national_code": (lambda o: getattr(o.user, "national_id", "") or "", "کد ملی")},
                    "search": ["user__username", "user__first_name", "user__last_name", "user__national_id"],
                    "qs": lambda: SchoolMembership.objects.select_related("user", "school")},
    "parent_links": {"label": "ارتباط اولیا و دانش‌آموزان", "section": "کاربران و دسترسی", "model": ParentLink,
                     "fields": ["parent", "student", "relation", "is_verified"], "list": ["parent", "student", "is_verified"],
                     "search": ["parent__username", "student__username"], "qs": lambda: ParentLink.objects.all()},
    "totp": {"label": "دستگاه‌های تأیید دو مرحله‌ای", "section": "کاربران و دسترسی", "model": TOTPDevice, "readonly": True,
             "fields": ["user", "confirmed", "created_at"], "list": ["user", "confirmed", "created_at"], "search": ["user__username"]},
    "provinces": {"label": "استان‌ها", "section": "ساختار سازمانی", "model": OrgUnit, "kind": "province",
                  "fields": ["name", "code"], "list": ["name", "code"], "search": ["name", "code"]},
    "counties": {"label": "شهرستان‌ها", "section": "ساختار سازمانی", "model": OrgUnit, "kind": "county",
                 "fields": ["name", "code", "parent"], "list": ["name", "code", "parent"], "search": ["name", "code"]},
    "regions": {"label": "نواحی و مناطق", "section": "ساختار سازمانی", "model": OrgUnit, "kind": "region",
                "fields": ["name", "code", "parent"], "list": ["name", "code", "parent"], "search": ["name", "code"]},
    "schools": {"label": "مدارس", "section": "ساختار سازمانی", "model": OrgUnit, "kind": "school",
                "fields": ["name", "code", "parent"], "list": ["name", "code", "parent"], "search": ["name", "code"]},
    "school_profiles": {"label": "مشخصات مدارس (کد، مقطع، نشانی)", "section": "ساختار سازمانی", "model": SchoolProfile,
                        "fields": ["school", "school_code", "level", "school_type", "shift", "ownership", "gender",
                                   "country", "county", "postal_code", "address", "phone", "fax", "website", "email"],
                        "list": ["school", "school_code", "level", "county"], "search": ["school_code"]},
    "levels": {"label": "مقاطع تحصیلی", "section": "کتابخانه آموزشی", "model": Level,
               "fields": ["code", "name", "order"], "list": ["code", "name", "order"], "search": ["name", "code"]},
    "grades": {"label": "پایه‌ها", "section": "کتابخانه آموزشی", "model": Grade,
               "fields": ["level", "code", "name", "order"], "list": ["code", "name", "level"], "search": ["name", "code"]},
    "fields": {"label": "رشته‌ها", "section": "کتابخانه آموزشی", "model": StudyField,
               "fields": ["code", "name", "level"], "list": ["code", "name", "level"], "search": ["name", "code"]},
    "subjects": {"label": "سرفصل دروس", "section": "کتابخانه آموزشی", "model": Subject,
                 "fields": ["code", "name", "grades", "study_fields", "weekly_hours", "subject_type", "kind"],
                 "list": ["code", "name", "weekly_hours"], "search": ["name", "code"]},
    "years": {"label": "سال‌های تحصیلی", "section": "مدارس و کلاس‌ها", "model": AcademicYear,
              "help": "سال تحصیلی با دو تاریخ تعریف می‌شود (شروع و پایان؛ مثلاً ۱۴۰۵/۰۷/۰۱ تا ۱۴۰۶/۰۶/۳۱). نام (۱۴۰۵-۱۴۰۶) خودکار ساخته می‌شود.",
              "fields": ["starts_on", "ends_on", "name", "is_current"], "list": ["name", "starts_on", "ends_on", "is_current"],
              "optional": ["name", "is_current"], "search": ["name"], "prepare": lambda o: _year_name(o)},
    "classes": {"label": "کلاس‌ها", "section": "مدارس و کلاس‌ها", "model": ClassRoom,
                "fields": ["school", "name", "code", "capacity", "grade", "study_field", "homeroom_teacher"],
                "list": ["name", "school", "grade", "academic_year"], "search": ["name", "code"]},
    "courses": {"label": "درس‌های کلاس", "section": "مدارس و کلاس‌ها", "model": Course,
                "fields": ["org", "title", "teacher", "subject", "classroom", "academic_year"],
                "list": ["title", "org", "teacher", "classroom"], "search": ["title"]},
    "enrollments": {"label": "ثبت‌نام دانش‌آموزان در درس", "section": "دروس و محتوا", "model": Enrollment,
                    "fields": ["course", "student", "academic_year"], "list": ["course", "student", "academic_year"],
                    "search": ["student__username"], "qs": lambda: Enrollment.objects.select_related("course", "student")},
    "content_items": {"label": "محتوای درسی", "section": "دروس و محتوا", "model": ContentItem,
                      "fields": ["course", "title", "kind", "body", "order", "is_published"],
                      "list": ["title", "course", "kind", "is_published"], "search": ["title"]},
    "assignments": {"label": "تکالیف", "section": "دروس و محتوا", "model": Assignment, "readonly": True,
                    "fields": ["course", "title", "due_at", "max_points", "is_published"], "list": ["title", "course", "due_at", "is_published"],
                    "search": ["title"]},
    "submissions": {"label": "پاسخ تکالیف", "section": "دروس و محتوا", "model": Submission, "readonly": True,
                    "fields": ["assignment", "student", "submitted_at", "is_late", "points"],
                    "list": ["assignment", "student", "submitted_at", "points"], "search": ["student__username"]},
    "live_sessions": {"label": "جلسات کلاس آنلاین", "section": "دروس و محتوا", "model": ClassSession, "readonly": True,
                      "fields": ["course", "title", "provider", "starts_at", "ends_at", "remote_status", "attendance_status"],
                      "list": ["title", "course", "starts_at", "remote_status"], "search": ["title"]},
    "exams": {"label": "آزمون‌ها", "section": "آزمون‌ها", "model": Exam, "readonly": True,
              "fields": ["course", "title", "assessment", "starts_at", "ends_at", "duration_minutes", "is_published"],
              "list": ["title", "course", "assessment", "starts_at", "is_published"], "search": ["title"]},
    "attempts": {"label": "شرکت‌ها در آزمون (نتایج)", "section": "آزمون‌ها", "model": Attempt, "readonly": True,
                 "fields": ["exam", "student", "submitted_at", "score", "total", "grading_status"],
                 "list": ["exam", "student", "score", "total"], "search": ["student__username"]},
    "appeals": {"label": "اعتراض‌های نمره", "section": "آزمون‌ها", "model": GradeAppeal, "readonly": True,
                "fields": ["student", "course", "kind", "status", "old_points", "new_points", "created_at"],
                "list": ["student", "course", "status", "created_at"], "search": ["student__username"]},
    "grade_changes": {"label": "تاریخچه تغییر نمره", "section": "گزارش‌ها و ممیزی", "model": GradeChange, "readonly": True,
                      "fields": ["kind", "ref_id", "student", "old_points", "new_points", "reason", "changed_by", "changed_at"],
                      "list": ["kind", "student", "old_points", "new_points", "changed_at"], "search": ["student__username"]},
    "attendance": {"label": "حضور و غیاب دانش‌آموزان", "section": "حضور، برنامه و انتقال", "model": AttendanceRecord, "readonly": True,
                   "fields": ["student", "classroom", "date", "period", "status", "source", "validation"],
                   "list": ["student", "classroom", "date", "status"], "search": ["student__username"]},
    "teacher_attendance": {"label": "حضور معلمان", "section": "حضور، برنامه و انتقال", "model": TeacherAttendance, "readonly": True,
                           "fields": ["teacher", "school", "date", "status", "note"],
                           "list": ["teacher", "school", "date", "status"], "search": []},
    "bell_periods": {"label": "زنگ‌های مدرسه", "section": "حضور، برنامه و انتقال", "model": BellPeriod,
                     "fields": ["school", "number", "start_time", "end_time"], "list": ["school", "number", "start_time"],
                     "search": ["school__name"]},
    "schedule": {"label": "برنامه هفتگی کلاس‌ها (فهرست)", "section": "حضور، برنامه و انتقال", "model": ScheduleEntry,
                 "fields": ["classroom", "weekday", "period", "course", "room"], "list": ["classroom", "weekday", "period", "course"],
                 "search": ["classroom__name"]},
    "calendar": {"label": "تقویم آموزشی و تعطیلات", "section": "حضور، برنامه و انتقال", "model": CalendarEvent,
                 "fields": ["org", "title", "kind", "start_date", "end_date", "blocks_attendance", "description"],
                 "list": ["title", "kind", "start_date", "end_date"], "search": ["title"]},
    "transfers": {"label": "انتقال دانش‌آموز", "section": "حضور، برنامه و انتقال", "model": TransferRequest, "readonly": True,
                  "fields": ["student", "from_school", "to_school", "status", "created_at"],
                  "list": ["student", "from_school", "to_school", "status"], "search": ["student__username"]},
    "school_history": {"label": "سوابق مدرسه دانش‌آموزان", "section": "حضور، برنامه و انتقال", "model": StudentSchoolHistory,
                       "readonly": True, "fields": ["student", "school", "start_date", "end_date", "reason", "transfer"],
                       "list": ["student", "school", "start_date", "end_date"], "search": ["student__username"]},
    "sms_gateways": {"label": "پنل‌های پیامکی", "section": "پیامک و امنیت", "model": SmsGateway,
                     "fields": ["school", "name", "base_url", "auth_header", "body_template", "sender", "is_active"],
                     "list": ["name", "school", "is_active", "last_test_ok"], "search": ["name"]},
    "sms_outbox": {"label": "صف پیامک والدین", "section": "پیامک و امنیت", "model": __import__("apps.sms.models", fromlist=["SmsOutbox"]).SmsOutbox,
                   "readonly": True, "fields": ["school", "student", "purpose", "mobile", "status", "attempts", "last_error", "created_at", "sent_at"],
                   "list": ["created_at", "student", "purpose", "status"], "search": []},
    "sms_logs": {"label": "گزارش پیامک‌ها", "section": "پیامک و امنیت", "model": SmsLog, "readonly": True,
                 "fields": ["gateway", "to_masked", "purpose", "accepted", "detail", "created_at"],
                 "list": ["created_at", "to_masked", "purpose", "accepted"], "search": ["to_masked"]},
    "audit_log": {"label": "گزارش ممیزی (سابقه کارها)", "section": "پیامک و امنیت", "model": AuditLog, "readonly": True,
                  "fields": ["user", "action", "target", "ip", "created_at"], "list": ["created_at", "user", "action", "target"],
                  "search": ["action", "target"]},
    "external_systems": {"label": "سامانه‌های خارجی", "section": "سامانه‌ها و عملیات", "model": ExternalSystem,
                         "fields": ["name", "kind", "base_url", "secret_env", "is_active", "notes"],
                         "list": ["name", "kind", "base_url", "is_active"], "search": ["name"]},
    "import_jobs": {"label": "ورودهای دسته‌ای", "section": "سامانه‌ها و عملیات", "model": ImportJob, "readonly": True,
                    "fields": ["school", "kind", "source_name", "status", "created_at", "committed_at"],
                    "list": ["created_at", "school", "kind", "status"], "search": ["source_name"]},
    "files": {"label": "فایل‌های بارگذاری‌شده", "section": "سامانه‌ها و عملیات", "model": FileAsset, "readonly": True,
              "fields": ["owner", "original_name", "kind", "size", "created_at"], "list": ["original_name", "kind", "size", "created_at"],
              "search": ["original_name"]},
    "provider_events": {"label": "رویدادهای سامانه‌های خارجی", "section": "سامانه‌ها و عملیات", "model": ProviderEvent, "readonly": True,
                        "fields": ["system", "event_type", "session", "occurred_at", "status", "error"],
                        "list": ["occurred_at", "system", "event_type", "status"], "search": ["event_type"]},
    "daily_stats": {"label": "آمار روزانه مدارس", "section": "گزارش‌ها و ممیزی", "model": DailyOrgStats, "readonly": True,
                    "fields": ["date", "org", "students", "att_total", "att_present", "exams_submitted", "submissions", "sessions"],
                    "list": ["date", "org", "students", "att_present"], "search": []},
}

# options lists for foreign keys (whitelist: model label -> queryset)
OPTION_MODELS = {
    "accounts.OrgUnit": lambda: OrgUnit.objects.all(),
    "accounts.User": lambda: User.objects.all(),
    "academics.Level": lambda: Level.objects.all(),
    "academics.Grade": lambda: Grade.objects.all(),
    "academics.StudyField": lambda: StudyField.objects.all(),
    "academics.AcademicYear": lambda: AcademicYear.objects.all(),
    "academics.Subject": lambda: Subject.objects.all(),
    "academics.ClassRoom": lambda: ClassRoom.objects.all(),
}


# ---------------------------------------------------------------------------------------------------------
# Pickers (how foreign keys are chosen) and role-dependent form rules. The browser builds the widgets from these;
# the server re-validates everything with the models' own rules.
SCHOOL = {"type": "org", "kind": "school"}
USER_ROLE_RULES = {
    "trigger": "role",
    "hidden": ["org", "expert_kind", "expert_level", "student_code", "father_name"],
    "roles": {
        "super_admin": {"show": []},
        "province_admin": {"show": ["org"], "org_kinds": ["province"], "required": ["org"]},
        "county_admin": {"show": ["org"], "org_kinds": ["county"], "required": ["org"]},
        "region_admin": {"show": ["org"], "org_kinds": ["region"], "required": ["org"]},
        "school_admin": {"show": ["org", "expert_level"], "org_kinds": ["school", "region"], "required": ["org", "expert_level"],
                         "hint": "برای مدیر مدرسه، منطقه (ناحیه) یا مدرسه را انتخاب کنید و مقطع را مشخص کنید."},
        "teacher": {"show": ["org"], "org_kinds": ["school"], "required": ["org"]},
        "school_deputy_edu": {"show": ["org"], "org_kinds": ["school"], "required": ["org"], "hint": "معاون آموزشی به مدرسه (یا ناحیه) وصل می‌شود."},
        "school_deputy_exec": {"show": ["org"], "org_kinds": ["school"], "required": ["org"], "hint": "معاون اجرایی به مدرسه وصل می‌شود."},
        "school_deputy_culture": {"show": ["org"], "org_kinds": ["school"], "required": ["org"], "hint": "معاون پرورشی به مدرسه وصل می‌شود."},
        "student": {"show": ["org", "student_code", "father_name"], "org_kinds": ["school"], "required": ["org", "student_code"]},
        "parent": {"show": []},
        "expert": {"show": ["org", "expert_kind", "expert_level"], "org_kinds": ["province", "county", "region"],
                   "required": ["org", "expert_kind", "expert_level"],
                   "hint": "نوع کارشناس (آموزش یا سنجش) و مقطع را انتخاب کنید؛ واحد: استان، شهرستان یا ناحیه."},
    },
}
PICKS = {
    "users": {"org": {"type": "org", "kinds_by_role": True}, "expert_level": {"type": "model", "model": "academics.Level"}},
    "memberships": {"user": {"type": "user", "role": "teacher,school_admin,school_deputy_edu,school_deputy_exec,school_deputy_culture"}, "school": SCHOOL},
    "parent_links": {"parent": {"type": "user", "role": "parent"}, "student": {"type": "user", "role": "student"}},
    "counties": {"parent": {"type": "org", "kind": "province"}},
    "regions": {"parent": {"type": "org", "kind": "province"}},
    "schools": {"parent": {"type": "org", "kind": "region"}},
    "school_profiles": {"school": SCHOOL, "level": {"type": "model", "model": "academics.Level", "combo": True},
                        "county": {"type": "org", "kind": "county"}},
    "grades": {"level": {"type": "model", "model": "academics.Level", "combo": True}},
    "fields": {"level": {"type": "model", "model": "academics.Level", "combo": True}},
    "classes": {"school": SCHOOL, "academic_year": {"type": "model", "model": "academics.AcademicYear"},
                "grade": {"type": "model", "model": "academics.Grade", "school_from": "school"},
                "study_field": {"type": "model", "model": "academics.StudyField", "school_from": "school"},
                "homeroom_teacher": {"type": "user", "role": "teacher", "school_from": "school"}},
    "courses": {"org": SCHOOL, "teacher": {"type": "user", "role": "teacher", "school_from": "org"},
                "subject": {"type": "model", "model": "academics.Subject", "school_from": "org"},
                "classroom": {"type": "model", "model": "academics.ClassRoom", "school_from": "org"},
                "academic_year": {"type": "model", "model": "academics.AcademicYear"}},
    "enrollments": {"course": {"type": "model", "model": "courses.Course"}, "student": {"type": "user", "role": "student"},
                    "academic_year": {"type": "model", "model": "academics.AcademicYear"}},
    "content_items": {"course": {"type": "model", "model": "courses.Course"}},
    "bell_periods": {"school": SCHOOL},
    "schedule": {"classroom": {"type": "model", "model": "academics.ClassRoom"}, "course": {"type": "model", "model": "courses.Course"},
                 "period": {"type": "model", "model": "timetable.BellPeriod"}},
    "calendar": {"org": {"type": "org", "kinds": ["province", "region", "school"], "nullable": True}},
    "sms_gateways": {"school": SCHOOL},
}
for _key, _pick in PICKS.items():
    RESOURCES[_key]["pick"] = _pick
for _r in User.ADMIN_ROLES:
    _e = USER_ROLE_RULES["roles"].setdefault(_r, {"show": [], "required": []})
    _e["show"] = list(_e.get("show", [])) + ["username"]
    _e["required"] = list(_e.get("required", [])) + ["username"]
USER_ROLE_RULES["hidden"] = list(USER_ROLE_RULES["hidden"]) + ["username"]
RESOURCES["users"]["rules"] = USER_ROLE_RULES


def _default_school_code(obj):
    """A school's code is entered once in «ساختار سازمانی» (the school's code); the profile takes it unless it has its own."""
    if not getattr(obj, "school_code", "") and getattr(obj, "school_id", None):
        code = OrgUnit.objects.filter(pk=obj.school_id).values_list("code", flat=True).first()
        if code:
            obj.school_code = code


RESOURCES["school_profiles"]["before_validate"] = _default_school_code
RESOURCES["content_items"]["rich"] = ["body"]


UNIT_LOOKUP = {"users": "org__path", "classes": "school__path", "courses": "org__path", "enrollments": "course__org__path",
               "memberships": "school__path", "school_profiles": "school__path", "bell_periods": "school__path",
               "schedule": "classroom__school__path", "content_items": "course__org__path", "sms_gateways": "school__path",
               "schools": "path", "counties": "path", "regions": "path", "attendance": "classroom__school__path",
               "teacher_attendance": "school__path", "school_history": "school__path", "transfers": "from_school__path",
               "calendar": "org__path", "assignments": "course__org__path", "exams": "course__org__path",
               "attempts": "exam__course__org__path", "parent_links": "student__org__path", "submissions": "assignment__course__org__path",
               "live_sessions": "course__org__path", "appeals": "course__org__path", "import_jobs": "school__path",
               "daily_stats": "org__path"}
for _k, _v in UNIT_LOOKUP.items():
    if _k in RESOURCES:
        RESOURCES[_k]["unit_lookup"] = _v
RESOURCES["users"]["filters"] = {"role": "role", "active": "is_active", "expert_kind": "expert_kind"}
RESOURCES["exams"]["filters"] = {"assessment": "assessment"}

RESOURCES["bell_periods"]["list"] = ["school", "number", "start_time", "end_time"]
RESOURCES["bell_periods"]["unit_lookup"] = "school__path"
RESOURCES["years"]["unit_lookup"] = None


# official holidays and occasions of the calendar (only the system administrator edits them)
from apps.schoolcal.models import Occasion as _Occasion  # noqa: E402
RESOURCES["occasions"] = {"label": "تعطیلات رسمی و مناسبت‌های تقویم", "section": "تقویم و تعطیلات", "model": _Occasion,
                          "fields": ["title", "jalali_year", "jalali_month", "jalali_day", "gregorian_date", "category", "is_holiday", "description", "is_active"],
                          "list": ["jalali_year", "jalali_month", "jalali_day", "title", "category", "is_holiday"],
                          "search": ["title"],
                          "help": "تعطیلات رسمی و مناسبت‌های ۱۴۰۵ و ۱۴۰۶ با seed_occasions وارد می‌شوند. موارد توضیح‌دار متن منبع نیاز به بررسی دارند."}


def _current_year(obj):
    """A class belongs to the current academic year; the form does not ask for it."""
    from apps.academics.models import AcademicYear
    if not getattr(obj, "academic_year_id", None):
        obj.academic_year = AcademicYear.objects.filter(is_current=True).first()


RESOURCES["classes"]["before_validate"] = _current_year

from apps.academics.models import GradeWeeklyHours as _GWH  # noqa: E402
from apps.timetable.models import TeacherAvailability as _TA  # noqa: E402
RESOURCES["approved_hours"] = {"label": "مصوبات ساعت هفتگی", "section": "کتابخانه آموزشی", "model": _GWH,
                               "fields": ["grade", "field", "subject", "hours"], "list": ["grade", "field", "subject", "hours"],
                               "search": []}
RESOURCES["teacher_presence"] = {"label": "ساعات حضور دبیران", "section": "مدارس و کلاس‌ها", "model": _TA,
                                 "fields": ["teacher", "weekday", "period"], "list": ["teacher", "weekday", "period"], "search": []}
PICKS["approved_hours"] = {"grade": {"type": "model", "model": "academics.Grade"},
                           "field": {"type": "model", "model": "academics.StudyField"},
                           "subject": {"type": "model", "model": "academics.Subject", "grade_from": "grade", "field_from": "field"}}
PICKS["teacher_presence"] = {"teacher": {"type": "user", "role": "teacher"},
                             "period": {"type": "model", "model": "timetable.BellPeriod"}}

for _key in ("approved_hours", "teacher_presence"):
    RESOURCES[_key]["pick"] = PICKS[_key]


from apps.accounts.models import RoleAssignment as _RA  # noqa: E402
RESOURCES["role_assignments"] = {"label": "نقش‌های اضافی کاربران", "section": "کاربران و دسترسی", "model": _RA,
                                 "fields": ["user", "role", "school"], "list": ["user", "role", "school"], "search": []}
RESOURCES["role_assignments"]["pick"] = {"user": {"type": "user"}, "school": {"type": "org", "kinds": ["school"], "nullable": True}}


from apps.exams.models import ExamVenue as _EV  # noqa: E402
RESOURCES["exam_venues"] = {"label": "محل‌های برگزاری امتحان", "section": "کتابخانه آموزشی", "model": _EV,
                            "fields": ["school", "name", "address"], "list": ["school", "name", "address"], "search": ["name"],
                            "pick": {"school": {"type": "org", "kinds": ["school"]}}}


from apps.academics.models import Branch as _Branch  # noqa: E402
RESOURCES["branches"] = {"label": "شاخه‌ها (زیر پایه‌ها)", "section": "کتابخانه آموزشی", "model": _Branch,
                         "fields": ["level", "name"], "list": ["level", "name"], "search": ["name"],
                         "pick": {"level": {"type": "model", "model": "academics.Level"}}}
