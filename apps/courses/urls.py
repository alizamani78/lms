from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import ContentItemView, CourseViewSet

router = SimpleRouter()
router.register("courses", CourseViewSet, basename="course")
urlpatterns = router.urls + [path("content/<int:pk>/", ContentItemView.as_view())]
