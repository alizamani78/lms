from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import BellPeriod, ScheduleEntry


@admin.register(BellPeriod)
class BellPeriodAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "school"
    read_roles = User.STAFF_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("school", "number", "start_time", "end_time")
    search_fields = ("school__name",)
    autocomplete_fields = ("school",)
    list_select_related = ("school",)


@admin.register(ScheduleEntry)
class ScheduleEntryAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "classroom__school"
    teacher_lookup = "course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("classroom", "weekday", "period", "course", "room")
    list_filter = ("weekday",)
    search_fields = ("classroom__name", "course__title")
    autocomplete_fields = ("classroom", "course")
    list_select_related = ("classroom", "period", "course")
