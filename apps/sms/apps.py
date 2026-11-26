from django.apps import AppConfig


class SmsConfig(AppConfig):
    name = "apps.sms"
    verbose_name = "پیامک و بازیابی رمز"

    def ready(self):
        from . import signals  # noqa: F401  (connects attendance and homework events to the parents' outbox)
