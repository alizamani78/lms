from django.urls import path

from .views import BellView, GridView, MyScheduleView

urlpatterns = [
    path("schedule/", MyScheduleView.as_view()),
    path("schedule/bell/<int:school_id>/", BellView.as_view()),
    path("schedule/grid/<int:classroom_id>/", GridView.as_view()),
]
