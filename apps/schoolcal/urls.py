from django.urls import path

from .views import CalendarDeleteView, CalendarView

from .views import OccasionListView

urlpatterns = [
    path("occasions/", OccasionListView.as_view()),
    path("calendar/", CalendarView.as_view()),
    path("calendar/<int:pk>/", CalendarDeleteView.as_view()),
]
