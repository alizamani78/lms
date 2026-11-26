from django.urls import path

from .activity_views import ActivityView

urlpatterns = [path("activity/", ActivityView.as_view())]
