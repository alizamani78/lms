from django.contrib import admin

from apps.core.jalali import jalali_str

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import Attempt, BankChoice, BankQuestion, Choice, Exam, Question


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 4
    max_num = 6

    def _locked(self, obj):
        return bool(obj and obj.exam.attempts.exists())

    def has_add_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)


@admin.register(Exam)
class ExamAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "course__org"
    teacher_lookup = "course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.STAFF_ROLES
    list_display = ("title", "course", "starts_jalali", "ends_jalali", "starts_at", "duration_minutes", "is_published")

    @admin.display(description="شروع (شمسی)")
    def starts_jalali(self, obj):
        return jalali_str(obj.starts_at, with_time=True)

    @admin.display(description="پایان (شمسی)")
    def ends_jalali(self, obj):
        return jalali_str(obj.ends_at, with_time=True)

    list_filter = ("is_published",)
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    list_select_related = ("course",)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.attempts.exists():          # timing is frozen once a student has started
            return ("course", "starts_at", "ends_at", "duration_minutes")
        return ()


@admin.register(Question)
class QuestionAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "exam__course__org"
    teacher_lookup = "exam__course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.STAFF_ROLES
    list_display = ("text", "exam", "order")
    search_fields = ("text", "exam__title")
    autocomplete_fields = ("exam",)
    list_select_related = ("exam",)
    inlines = [ChoiceInline]

    def _locked(self, obj):
        return bool(obj and obj.exam.attempts.exists())

    def has_change_permission(self, request, obj=None):      # questions are frozen once attempts exist
        return not self._locked(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._locked(obj) and super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        obj.exam.save(update_fields=["updated_at"])         # refresh the cached question payload


@admin.register(Attempt)
class AttemptAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Read-only results view."""
    scope_lookup = "exam__course__org"
    teacher_lookup = "exam__course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = ()
    list_display = ("student", "exam", "started_at", "submitted_at", "score", "total")
    search_fields = ("student__username", "exam__title")
    list_select_related = ("student", "exam")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class BankChoiceInline(admin.TabularInline):
    model = BankChoice
    extra = 4
    max_num = 6


@admin.register(BankQuestion)
class BankQuestionAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Teachers manage the bank through the API; admins can also use this page."""
    scope_lookup = "org"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("text", "kind", "org", "subject", "grade", "points", "is_active")
    list_filter = ("kind", "grade")
    search_fields = ("text",)
    autocomplete_fields = ("org", "subject", "grade")
    inlines = [BankChoiceInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
