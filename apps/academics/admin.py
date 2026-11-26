from django import forms
from django.contrib import admin

from apps.core.jalali import jalali_str
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import AcademicYear, ClassRoom, Grade, Level, StudyField, Subject


class ReferenceAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Reference data: every admin can read it, only super admins can change it."""
    global_data = True
    read_roles = User.ADMIN_ROLES
    write_roles = ()


@admin.register(AcademicYear)
class AcademicYearAdmin(ReferenceAdmin):
    list_display = ("name", "starts_on", "start_jalali", "ends_on", "end_jalali", "is_current")

    @admin.display(description="شروع (شمسی)")
    def start_jalali(self, obj):
        return jalali_str(obj.starts_on)

    @admin.display(description="پایان (شمسی)")
    def end_jalali(self, obj):
        return jalali_str(obj.ends_on)

    search_fields = ("name",)


@admin.register(Level)
class LevelAdmin(ReferenceAdmin):
    list_display = ("name", "order")
    search_fields = ("name",)


@admin.register(Grade)
class GradeAdmin(ReferenceAdmin):
    list_display = ("name", "level", "order")
    list_filter = ("level",)
    search_fields = ("name",)
    autocomplete_fields = ("level",)


@admin.register(StudyField)
class StudyFieldAdmin(ReferenceAdmin):
    list_display = ("name", "level")
    search_fields = ("name",)
    autocomplete_fields = ("level",)


@admin.register(Subject)
class SubjectAdmin(ReferenceAdmin):
    """Shared subjects: pick several grades and several fields (checked against each other on save)."""
    list_display = ("name", "code", "grades_text", "fields_text", "weekly_hours")
    list_filter = ("grades__level", "grades")
    search_fields = ("name", "code")
    filter_horizontal = ("grades", "study_fields")

    @admin.display(description="پایه‌ها")
    def grades_text(self, obj):
        return "، ".join(g.name for g in obj.grades.all())

    @admin.display(description="رشته‌ها")
    def fields_text(self, obj):
        return "، ".join(f.name for f in obj.study_fields.all())

    def save_related(self, request, form, formsets, change):
        from django.core.exceptions import ValidationError as VE
        super().save_related(request, form, formsets, change)
        problems = form.instance.link_problems(list(form.instance.grades.all()), list(form.instance.study_fields.all()))
        if problems:
            raise VE(problems)


class ClassRoomForm(forms.ModelForm):
    class Meta:
        model = ClassRoom
        fields = "__all__"

    def clean(self):
        data = super().clean()
        school, students = data.get("school"), data.get("students")
        if school and students:
            bad = [s.username for s in students if s.role != "student" or s.org_id != school.pk]
            if bad:
                raise ValidationError("این دانش‌آموزان به مدرسه کلاس تعلق ندارند: " + "، ".join(bad[:10]))
        return data


@admin.register(ClassRoom)
class ClassRoomAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "school"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    form = ClassRoomForm
    list_display = ("name", "school", "academic_year", "grade", "study_field")
    list_filter = ("academic_year", "grade")
    search_fields = ("name", "school__name")
    autocomplete_fields = ("school", "academic_year", "grade", "study_field", "homeroom_teacher", "students")
    list_select_related = ("school", "academic_year", "grade", "study_field")
