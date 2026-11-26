from django.contrib import admin

from .models import EvaluationPolicy


@admin.register(EvaluationPolicy)
class EvaluationPolicyAdmin(admin.ModelAdmin):
    """Read-only overview; policies are changed through the API by the level's own admins."""
    list_display = ("org", "pass_percent", "updated_at")
    readonly_fields = [f.name for f in EvaluationPolicy._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
