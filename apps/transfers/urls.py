from django.urls import path

from .views import (AcceptView, CancelView, CompleteView, ReleaseView, SchoolSearchView, StudentSearchView,
                    TransferListCreateView)

urlpatterns = [
    path("transfers/", TransferListCreateView.as_view()),
    path("transfers/<int:pk>/release/", ReleaseView.as_view()),
    path("transfers/<int:pk>/accept/", AcceptView.as_view()),
    path("transfers/<int:pk>/complete/", CompleteView.as_view()),
    path("transfers/<int:pk>/cancel/", CancelView.as_view()),
    path("students/", StudentSearchView.as_view()),
    path("schools/search/", SchoolSearchView.as_view()),
]
