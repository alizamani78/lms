from django.urls import path

from .views import RecordsBulkView, FactorDetailView, FactorsView, RecordDetailView, RecordsView, WorksheetView

urlpatterns = [
    path("discipline/records/bulk/", RecordsBulkView.as_view()),
    path("discipline/factors/", FactorsView.as_view()),
    path("discipline/factors/<int:pk>/", FactorDetailView.as_view()),
    path("discipline/records/", RecordsView.as_view()),
    path("discipline/records/<int:pk>/", RecordDetailView.as_view()),
    path("discipline/worksheet/<int:pk>/", WorksheetView.as_view()),
]
