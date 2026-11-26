from django.urls import path

from .report_views import ReportCatalogView, ReportRunView, ScopeOrgsView

urlpatterns = [
    path("reports/catalog/", ReportCatalogView.as_view()),
    path("reports/scope-orgs/", ScopeOrgsView.as_view()),
    path("reports/run/<str:key>/", ReportRunView.as_view()),
]
