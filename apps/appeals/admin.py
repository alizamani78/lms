from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import AppealEvent, GradeAppeal


class EventInline(admin.TabularInline):
    model = AppealEvent
    extra = 0
    can_delete = False
    readonly_fields = ("actor", "action", "note", "at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GradeAppeal)
class GradeAppealAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only overview; decisions go through the API so grades and history stay consistent."""
    scope_lookup = "course__org"
    teacher_lookup = "course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = ()
    list_display = ("student", "course", "kind", "status", "old_points", "new_points", "created_at")
    list_filter = ("status", "kind")
    search_fields = ("student__username", "course__title")
    list_select_related = ("student", "course")
    inlines = [EventInline]
    readonly_fields = [f.name for f in GradeAppeal._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
