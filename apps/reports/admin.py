from django.contrib import admin

from .models import DailyOrgStats


@admin.register(DailyOrgStats)
class DailyOrgStatsAdmin(admin.ModelAdmin):
    """Read-only view of the pre-computed table (super admins)."""
    list_display = ("date", "org", "students", "att_total", "att_present", "exams_submitted", "submissions", "sessions")
    list_filter = ("date",)
    readonly_fields = [f.name for f in DailyOrgStats._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
