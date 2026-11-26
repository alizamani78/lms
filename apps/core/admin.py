from django.contrib import admin

from .models import AuditLog, GradeChange


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "target", "ip")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GradeChange)
class GradeChangeAdmin(admin.ModelAdmin):
    """Read-only, super admins only."""
    list_display = ("changed_at", "kind", "ref_id", "student", "old_points", "new_points", "changed_by", "reason")
    list_filter = ("kind",)
    search_fields = ("student__username", "reason")
    readonly_fields = [f.name for f in GradeChange._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
