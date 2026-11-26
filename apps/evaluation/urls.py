from django.urls import path

from .views import EvaluationPolicyView, MyEvaluationView

from .classgrades import ClassGradeBulkView, ClassGradeDeleteView, ClassWorksheetView, GraceBulkView

urlpatterns = [
    path("class-grades/bulk/", ClassGradeBulkView.as_view()),
    path("class-grades/grace/", GraceBulkView.as_view()),
    path("class-grades/<int:pk>/", ClassGradeDeleteView.as_view()),
    path("classes/<int:pk>/worksheet/", ClassWorksheetView.as_view()),
    path("evaluation-policy/", EvaluationPolicyView.as_view()),
    path("me/evaluation/", MyEvaluationView.as_view()),
]
