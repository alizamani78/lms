from django.urls import path

from .views import DeleteView, DownloadView, UploadView

urlpatterns = [
    path("files/", UploadView.as_view()),
    path("files/<int:pk>/download/", DownloadView.as_view()),
    path("files/<int:pk>/", DeleteView.as_view()),
]
