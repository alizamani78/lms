from django.urls import path

from .recovery import RecoveryRequestView, RecoveryVerifyView
from .views import SchoolSmsDetailView, SchoolSmsLogView, SchoolSmsTestView, SchoolSmsView

urlpatterns = [
    path("recovery/request/", RecoveryRequestView.as_view()),
    path("recovery/verify/", RecoveryVerifyView.as_view()),
    path("schools/<int:pk>/sms/", SchoolSmsView.as_view()),
    path("schools/<int:pk>/sms/<int:gid>/", SchoolSmsDetailView.as_view()),
    path("schools/<int:pk>/sms/<int:gid>/test/", SchoolSmsTestView.as_view()),
    path("schools/<int:pk>/sms-log/", SchoolSmsLogView.as_view()),
]
