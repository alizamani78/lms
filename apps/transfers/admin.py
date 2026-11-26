from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import TransferEvent, TransferRequest


class EventInline(admin.TabularInline):
    model = TransferEvent
    extra = 0
    can_delete = False
    readonly_fields = ("actor", "action", "note", "at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TransferRequest)
class TransferRequestAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only: transfers run through the API's approval steps."""
    scope_lookup = "to_school"
    read_roles = User.ADMIN_ROLES
    write_roles = ()
    list_display = ("student", "from_school", "to_school", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("student__username",)
    list_select_related = ("student", "from_school", "to_school")
    inlines = [EventInline]
    readonly_fields = [f.name for f in TransferRequest._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
