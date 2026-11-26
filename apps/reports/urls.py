from django.urls import path

from .views import ExamReportView, ExamResultsCsvView, OrgStatsView, OrgSummaryView

urlpatterns = [
    path("reports/exams/<int:pk>/", ExamReportView.as_view()),
    path("reports/exams/<int:pk>/results.csv", ExamResultsCsvView.as_view()),
    path("reports/orgs/<int:pk>/", OrgSummaryView.as_view()),
    path("reports/orgs/<int:pk>/stats/", OrgStatsView.as_view()),
]
