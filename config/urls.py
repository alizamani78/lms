from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import LoginView, LogoutView, RefreshView
from apps.core.views import healthz, readyz
from apps.core.web import app, system_panel

urlpatterns = [
    path("", app, name="home"),
    path("system/", system_panel, name="system-panel"),
    *([path(settings.ADMIN_URL, admin.site.urls)] if getattr(settings, "ADMIN_ENABLED", True) else []),
    path("healthz", healthz),
    path("readyz", readyz),
    path("api/auth/login/", LoginView.as_view()),
    path("api/auth/refresh/", RefreshView.as_view()),
    path("api/auth/logout/", LogoutView.as_view()),
    path("api/", include("apps.accounts.urls")),
    path("api/", include("apps.courses.urls")),
    path("api/", include("apps.exams.urls")),
    path("api/", include("apps.coursework.urls")),
    path("api/", include("apps.attendance.urls")),
    path("api/", include("apps.timetable.urls")),
    path("api/", include("apps.schoolcal.urls")),
    path("api/", include("apps.appeals.urls")),
    path("api/", include("apps.transfers.urls")),
    path("api/", include("apps.files.urls")),
    path("api/", include("apps.imports.urls")),
    path("api/", include("apps.academics.urls")),
    path("api/", include("apps.sms.urls")),
    path("api/", include("apps.sysadmin.urls")),
    path("api/", include("apps.reports.report_urls")),
    path("api/", include("apps.attendance.teacher_urls")),
    path("api/", include("apps.evaluation.urls")),
    path("api/", include("apps.core.activity_urls")),
    path("api/", include("apps.core.monitor_urls")),
    path("api/", include("apps.discipline.urls")),
    path("api/", include("apps.cultural.urls")),
    path("api/", include("apps.meetings.urls")),
    path("api/", include("apps.processbook.urls")),
    path("api/", include("apps.liveclass.urls")),
    path("api/", include("apps.reports.urls")),
]
