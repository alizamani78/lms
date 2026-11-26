from django.urls import path

from .views import MonitorHistoryView, MonitorNowView, MonitorSettingView

urlpatterns = [
    path("monitor/now/", MonitorNowView.as_view()),
    path("monitor/history/", MonitorHistoryView.as_view()),
    path("monitor/setting/", MonitorSettingView.as_view()),
]
