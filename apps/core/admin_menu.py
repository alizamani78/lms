"""Groups the Django admin menu into clear sections (Persian titles, fixed order).
Models not listed here fall into «سایر». Only models the user may see are shown (Django's own permission check)."""
import types

from django.urls import reverse

SECTIONS = [
    ("کاربران و دسترسی", [("accounts", "User"), ("accounts", "SchoolMembership"), ("accounts", "ParentLink"),
                          ("accounts", "TOTPDevice")]),
    ("ساختار سازمانی", [("accounts", "Province"), ("accounts", "County"), ("accounts", "Region"),
                        ("accounts", "School"), ("accounts", "SchoolProfile"), ("accounts", "OrgUnit")]),
    ("مدارس و کلاس‌ها", [("academics", "ClassRoom"), ("academics", "AcademicYear"), ("schoolcal", "CalendarEvent")]),
    ("کتابخانه آموزشی", [("academics", "Level"), ("academics", "Grade"), ("academics", "StudyField"),
                         ("academics", "Subject")]),
    ("دروس و محتوا", [("courses", "Course"), ("courses", "Enrollment"), ("courses", "ContentItem"),
                      ("coursework", "Assignment"), ("coursework", "Submission"), ("liveclass", "ClassSession")]),
    ("آزمون‌ها", [("exams", "Exam"), ("exams", "Question"), ("exams", "BankQuestion"), ("exams", "Attempt"),
                  ("appeals", "GradeAppeal"), ("core", "GradeChange")]),
    ("حضور، برنامه و انتقال", [("attendance", "AttendanceRecord"), ("timetable", "ScheduleEntry"),
                               ("timetable", "BellPeriod"), ("transfers", "TransferRequest")]),
    ("پیامک و امنیت", [("sms", "SmsGateway"), ("sms", "SmsLog"), ("sms", "PasswordResetCode"),
                       ("core", "AuditLog")]),
    ("سامانه‌های خارجی و ورود دسته‌ای", [("integrations", "ExternalSystem"), ("liveclass", "ProviderEvent"),
                                         ("imports", "ImportJob"), ("files", "FileAsset")]),
    ("گزارش‌ها", [("reports", "DailyOrgStats")]),
]


def grouped_app_list(self, request, app_label=None):
    base = self._build_app_dict(request)
    index = {}
    for app in base.values():
        for m in app["models"]:
            index[(app["app_label"], m["object_name"])] = m
    used, out = set(), []
    for title, keys in SECTIONS:
        models = []
        for key in keys:
            if key in index and key not in used:
                models.append(index[key])
                used.add(key)
        if models:
            out.append({"name": title, "app_label": title, "app_url": "#", "has_module_perms": True, "models": models})
    rest = [m for k, m in index.items() if k not in used]
    if rest:
        out.append({"name": "سایر", "app_label": "other", "app_url": "#", "has_module_perms": True, "models": rest})
    return out


def install(site):
    site.get_app_list = types.MethodType(grouped_app_list, site)
