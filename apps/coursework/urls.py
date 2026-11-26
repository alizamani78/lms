from django.urls import path

from .views import (AssignmentCreateView, AssignmentListView, AssignmentManageView, GradeSubmissionView,
                    SubmissionListView, SubmitAssignmentView)

urlpatterns = [
    path("assignments/", AssignmentListView.as_view()),
    path("courses/<int:course_id>/assignments/", AssignmentCreateView.as_view()),
    path("assignments/<int:pk>/", AssignmentManageView.as_view()),
    path("assignments/<int:pk>/submit/", SubmitAssignmentView.as_view()),
    path("assignments/<int:pk>/submissions/", SubmissionListView.as_view()),
    path("submissions/<int:pk>/grade/", GradeSubmissionView.as_view()),
]
