from django.urls import path

from .views import (AppealDetailView, AppealListCreateView, DecideView, EligibleView, EscalateView, WithdrawView)

urlpatterns = [
    path("appeals/", AppealListCreateView.as_view()),
    path("appeals/eligible/", EligibleView.as_view()),
    path("appeals/<int:pk>/", AppealDetailView.as_view()),
    path("appeals/<int:pk>/decide/", DecideView.as_view()),
    path("appeals/<int:pk>/escalate/", EscalateView.as_view()),
    path("appeals/<int:pk>/withdraw/", WithdrawView.as_view()),
]
