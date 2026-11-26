from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import ContentItem, Course, Enrollment


@admin.register(Course)
class CourseAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "org"
    teacher_lookup = "teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("title", "org", "subject", "classroom", "teacher")
    list_filter = ("academic_year", "classroom__grade")
    search_fields = ("title", "org__name")
    autocomplete_fields = ("org", "teacher", "subject", "classroom")
    list_select_related = ("org", "teacher", "subject", "classroom")
    actions = ["enroll_classroom_students"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "teacher":
            kwargs["queryset"] = User.objects.filter(role=User.Role.TEACHER)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.action(description="ثبت‌نام همه دانش‌آموزان کلاس مرتبط در درس‌های انتخاب‌شده", permissions=["change"])
    def enroll_classroom_students(self, request, queryset):
        total = 0
        for course in queryset.exclude(classroom=None).select_related("classroom"):
            ids = list(course.classroom.students.values_list("pk", flat=True))
            Enrollment.objects.bulk_create([Enrollment(course=course, student_id=i, academic_year_id=course.academic_year_id) for i in ids],
                                           ignore_conflicts=True)
            total += len(ids)
        self.message_user(request, f"{total} ثبت‌نام بررسی شد (موارد تکراری نادیده گرفته می‌شوند).")


@admin.register(Enrollment)
class EnrollmentAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "course__org"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("student", "course", "academic_year")
    list_filter = ("academic_year",)
    search_fields = ("student__username", "course__title")
    autocomplete_fields = ("course", "student")
    list_select_related = ("student", "course")


@admin.register(ContentItem)
class ContentItemAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "course__org"
    teacher_lookup = "course__teacher"
    read_roles = User.STAFF_ROLES
    write_roles = User.STAFF_ROLES
    list_display = ("title", "course", "kind", "order", "is_published")
    list_filter = ("kind", "is_published")
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    list_select_related = ("course",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
