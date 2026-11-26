from django.urls import path

from .views import (AttendanceView, CandidatesView, DecisionDetailView, DecisionsView, InvitationPrintView, InvitationsView,
                    MeetingDetailView, MeetingsView, MyMeetingsView)

urlpatterns = [
    path("meetings/", MeetingsView.as_view()),
    path("meetings/candidates/", CandidatesView.as_view()),
    path("meetings/mine/", MyMeetingsView.as_view()),
    path("meetings/<int:pk>/", MeetingDetailView.as_view()),
    path("meetings/<int:pk>/invitations/", InvitationsView.as_view()),
    path("meetings/<int:pk>/invitation/<int:user_id>/", InvitationPrintView.as_view()),
    path("meetings/<int:pk>/attendance/", AttendanceView.as_view()),
    path("meetings/<int:pk>/decisions/", DecisionsView.as_view()),
    path("meetings/decisions/<int:pk>/", DecisionDetailView.as_view()),
]
