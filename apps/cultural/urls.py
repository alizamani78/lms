from django.urls import path

from .views import (CompetitionConsentFormView, CompetitionDetailView, CompetitionParticipantsView, CompetitionResultsView,
                    CompetitionsView, ConsentFormView, TripParticipantsView,
                    TripsView, ConsentPrintLinkView, ConsentPublicPrintView)

urlpatterns = [
    path("cultural/trips/<int:pk>/consent/<int:student_id>/print-link/", ConsentPrintLinkView.as_view()),
    path("cultural/consent-print/<str:token>/", ConsentPublicPrintView.as_view()),
    path("cultural/competitions/", CompetitionsView.as_view()),
    path("cultural/competitions/<int:pk>/", CompetitionDetailView.as_view()),
    path("cultural/competitions/<int:pk>/results/", CompetitionResultsView.as_view()),
    path("cultural/competitions/<int:pk>/participants/", CompetitionParticipantsView.as_view()),
    path("cultural/competitions/<int:pk>/consent/<int:student_id>/", CompetitionConsentFormView.as_view()),
    path("cultural/trips/", TripsView.as_view()),
    path("cultural/trips/<int:pk>/participants/", TripParticipantsView.as_view()),
    path("cultural/trips/<int:pk>/consent/<int:student_id>/", ConsentFormView.as_view()),
]
