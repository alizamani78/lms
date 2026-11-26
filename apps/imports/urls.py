from django.urls import path

from .catalog_views import CatalogCommitView, CatalogPreviewView
from .views import ImportCommitView, ImportCreateView, ImportDetailView, ImportListView, ImportRowsView, MySchoolsView

urlpatterns = [
    path("imports/", ImportListView.as_view()),
    path("imports/create/", ImportCreateView.as_view()),
    path("imports/<int:pk>/", ImportDetailView.as_view()),
    path("imports/<int:pk>/rows/", ImportRowsView.as_view()),
    path("imports/<int:pk>/commit/", ImportCommitView.as_view()),
    path("me/schools/", MySchoolsView.as_view()),
    path("catalog-import/preview/", CatalogPreviewView.as_view()),
    path("catalog-import/commit/", CatalogCommitView.as_view()),
]
