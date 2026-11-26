from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    verbose_name = "گزارش ممیزی"

    def ready(self):
        from django.contrib import admin

        from .admin_mfa import MfaAdminLoginForm
        from .admin_menu import install
        admin.site.site_header = "پنل مدیریت سامانه آموزش رضوی"
        admin.site.site_title = "مدیریت آموزش رضوی"
        admin.site.index_title = "مدیریت یکپارچه: کاربران، مدارس، کلاس‌ها، دروس و گزارش‌ها"
        admin.site.login_form = MfaAdminLoginForm
        admin.site.login_template = "mfa_admin_login.html"
        install(admin.site)
