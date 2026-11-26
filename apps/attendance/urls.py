from django.urls import path

from .views import AttendanceView, ClassroomListView, MyAttendanceView, RosterView

urlpatterns = [
    path("attendance/", AttendanceView.as_view()),
    path("me/attendance/", MyAttendanceView.as_view()),
    path("classrooms/", ClassroomListView.as_view()),
    path("classrooms/<int:pk>/students/", RosterView.as_view()),
]
