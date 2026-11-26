from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin
from apps.core.jalali import jalali_str

from .models import CalendarEvent


@admin.register(CalendarEvent)
class CalendarEventAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "org"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("title", "kind", "org", "start_jalali", "end_jalali", "start_date", "end_date", "blocks_attendance")
    list_filter = ("kind", "blocks_attendance")
    search_fields = ("title",)
    autocomplete_fields = ("org",)

    @admin.display(description="از (شمسی)")
    def start_jalali(self, obj):
        return jalali_str(obj.start_date)

    @admin.display(description="تا (شمسی)")
    def end_jalali(self, obj):
        return jalali_str(obj.end_date)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
