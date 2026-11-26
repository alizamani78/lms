from django.contrib import admin

from apps.core.jalali import jalali_str

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import Assignment, Submission


@admin.register(Assignment)
class AssignmentAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "course__org"
    teacher_lookup = "course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.STAFF_ROLES
    list_display = ("title", "course", "due_jalali", "due_at", "max_points", "is_published")

    @admin.display(description="مهلت (شمسی)")
    def due_jalali(self, obj):
        return jalali_str(obj.due_at, with_time=True)

    list_filter = ("is_published",)
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    list_select_related = ("course",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Submission)
class SubmissionAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only: grading goes through the API so every change reaches the grade history."""
    scope_lookup = "assignment__course__org"
    teacher_lookup = "assignment__course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = ()
    list_display = ("student", "assignment", "submitted_at", "is_late", "points")
    search_fields = ("student__username", "assignment__title")
    list_select_related = ("student", "assignment")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
