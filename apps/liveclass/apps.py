from django.apps import AppConfig


class LiveclassConfig(AppConfig):
    name = "apps.liveclass"
    verbose_name = "کلاس‌های آنلاین"

    def ready(self):
        from . import signals  # noqa: F401
