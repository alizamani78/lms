from django.urls import path

from .hooks import ProviderWebhookView
from .management_views import AttendanceExcelView, AttendancePrintLinkView, AttendancePublicPrintView, RecordingItemView, RecordingListView, SessionItemView
from .views import JoinView, LiveListView, SessionCreateView

urlpatterns = [
    path("live/", LiveListView.as_view()),
    path("live/<int:pk>/join/", JoinView.as_view()),
    path("courses/<int:course_id>/sessions/", SessionCreateView.as_view()),
    path("hooks/<int:system_id>/", ProviderWebhookView.as_view()),
    path("live/<int:pk>/", SessionItemView.as_view()),
    path("live/<int:pk>/recordings/", RecordingListView.as_view()),
    path("live/<int:pk>/attendance.xlsx", AttendanceExcelView.as_view()),
    path("live/<int:pk>/attendance/print-link/", AttendancePrintLinkView.as_view()),
    path("live-attendance-print/<str:token>/", AttendancePublicPrintView.as_view()),
    path("live-recordings/<int:pk>/", RecordingItemView.as_view()),
]
