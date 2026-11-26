from django.contrib import admin

from apps.core.jalali import jalali_str

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import ClassSession, OnlinePresence, ProviderEvent


@admin.register(ClassSession)
class ClassSessionAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "course__org"
    teacher_lookup = "course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.STAFF_ROLES
    list_display = ("title", "course", "provider", "system", "starts_jalali", "starts_at", "remote_status", "attendance_status")

    @admin.display(description="شروع (شمسی)")
    def starts_jalali(self, obj):
        return jalali_str(obj.starts_at, with_time=True)

    readonly_fields = ("remote_status", "attendance_status", "last_error", "create_attempts")
    actions = ["requeue_creation"]
    list_filter = ("provider",)
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    list_select_related = ("course", "system")

    @admin.action(description="ارسال دوباره به صف ساخت اتاق", permissions=["change"])
    def requeue_creation(self, request, queryset):
        from .tasks import create_remote_meeting
        n = 0
        for s in queryset.filter(auto_create=True).exclude(remote_status="ready"):
            ClassSession.objects.filter(pk=s.pk).update(remote_status="pending", create_attempts=0, last_error="")
            create_remote_meeting.delay(s.pk)
            n += 1
        self.message_user(request, f"{n} جلسه دوباره در صف قرار گرفت.")


@admin.register(ProviderEvent)
class ProviderEventAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only audit of what the providers sent (super admins)."""
    read_roles = ()
    write_roles = ()
    list_display = ("received_at", "system", "event_type", "session", "status")
    list_filter = ("event_type", "status")
    readonly_fields = [f.name for f in ProviderEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
