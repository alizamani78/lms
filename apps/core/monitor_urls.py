from django.urls import path

from .monitor_views import MonitorView

urlpatterns = [path("system/monitor/", MonitorView.as_view())]
