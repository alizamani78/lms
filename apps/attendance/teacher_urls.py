from django.urls import path

from .teacher_views import TeacherAttendanceView

urlpatterns = [path("teacher-attendance/", TeacherAttendanceView.as_view())]
