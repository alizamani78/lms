from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import ImportJob


@admin.register(ImportJob)
class ImportJobAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only audit of bulk imports (rows are kept for the preview only)."""
    scope_lookup = "school"
    read_roles = User.ADMIN_ROLES
    write_roles = ()
    list_display = ("created_at", "school", "kind", "status", "created_by", "source_name")
    list_filter = ("kind", "status")
    readonly_fields = [f.name for f in ImportJob._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
