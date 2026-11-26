from django.urls import path

from .me import MeView, PasswordView, SchoolListView
from .reset import ResetPasswordView
from apps.transfers.history_views import StudentHistoryView
from .geo import CountyDetailView, CountyListView, GeoView, RegionDetailView, RegionListView
from .photos import PhotoView, PhotoZipView
from .school_profile_api import CascadeView, SchoolProfileView
from .mfa_views import MfaConfirmView, MfaDisableView, MfaSetupView
from .parent import ChildOverviewView, ChildrenView

urlpatterns = [
    path("me/", MeView.as_view()),
    path("me/password/", PasswordView.as_view()),
    path("schools/", SchoolListView.as_view()),
    path("users/<int:pk>/reset-password/", ResetPasswordView.as_view()),
    path("geo/", GeoView.as_view()),
    path("students/<int:pk>/history/", StudentHistoryView.as_view()),
    path("geo/counties/", CountyListView.as_view()),
    path("geo/counties/<int:pk>/", CountyDetailView.as_view()),
    path("geo/regions/", RegionListView.as_view()),
    path("geo/regions/<int:pk>/", RegionDetailView.as_view()),
    path("users/<int:pk>/photo/", PhotoView.as_view()),
    path("photos/zip/", PhotoZipView.as_view()),
    path("schools/<int:pk>/profile/", SchoolProfileView.as_view()),
    path("school-form/", CascadeView.as_view()),
    path("mfa/setup/", MfaSetupView.as_view()),
    path("mfa/confirm/", MfaConfirmView.as_view()),
    path("mfa/disable/", MfaDisableView.as_view()),
    path("children/", ChildrenView.as_view()),
    path("children/<int:student_id>/overview/", ChildOverviewView.as_view()),
]
