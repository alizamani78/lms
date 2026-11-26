from django.contrib import admin

from apps.core.jalali import jalali_str

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import AttendanceChange, AttendanceRecord


class ChangeInline(admin.TabularInline):
    model = AttendanceChange
    extra = 0
    can_delete = False
    readonly_fields = ("old_status", "new_status", "reason", "changed_by", "changed_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only for everyone (including superusers): corrections go through the API so they are logged."""
    scope_lookup = "classroom__school"
    teacher_lookup = None
    read_roles = User.ADMIN_ROLES
    write_roles = ()
    list_display = ("student", "classroom", "date_jalali", "date", "period", "status", "source", "validation")

    @admin.display(description="تاریخ (شمسی)")
    def date_jalali(self, obj):
        return jalali_str(obj.date)

    list_filter = ("status", "source", "validation", "date")
    search_fields = ("student__username", "classroom__name")
    list_select_related = ("student", "classroom")
    inlines = [ChangeInline]
    readonly_fields = [f.name for f in AttendanceRecord._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import TeacherAttendance  # noqa: E402


@admin.register(TeacherAttendance)
class TeacherAttendanceAdmin(admin.ModelAdmin):
    """Read-only: changes go through the API so they are logged."""
    list_display = ("teacher", "school", "date", "status")
    readonly_fields = [f.name for f in TeacherAttendance._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
