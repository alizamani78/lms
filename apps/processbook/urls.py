from django.urls import path

from .views import DefinitionDetailView, DefinitionsView, EntriesView, EvaluationsView, StudentBookView

urlpatterns = [
    path("processbook/definitions/", DefinitionsView.as_view()),
    path("processbook/definitions/<int:pk>/", DefinitionDetailView.as_view()),
    path("processbook/entries/", EntriesView.as_view()),
    path("processbook/student/<int:pk>/", StudentBookView.as_view()),
    path("processbook/evaluations/", EvaluationsView.as_view()),
]
