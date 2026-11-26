from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied

from apps.core.admin_base import ScopedAdminMixin

from .models import County, OrgUnit, ParentLink, Province, Region, School, SchoolMembership, SchoolProfile, TOTPDevice, User


class OrgUnitBaseAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "self"
    read_roles = User.ADMIN_ROLES
    write_roles = ()                       # overridden per kind
    search_fields = ("name",)
    autocomplete_fields = ("parent",)
    fields = ("name", "parent")
    list_display = ("name", "parent")
    list_select_related = ("parent",)

    def _orgs_to_check(self, obj):
        """A unit is writable only if both its current position and its (new) parent are inside the scope."""
        orgs = [obj.parent] if obj.parent_id else []
        if obj.pk:
            orgs.append(OrgUnit.objects.get(pk=obj.pk))
        return orgs


@admin.register(OrgUnit)
class OrgUnitAdmin(OrgUnitBaseAdmin):
    """Registered for autocomplete lookups and a superuser overview; hidden from the main menu."""
    list_display = ("name", "kind", "parent")
    list_filter = ("kind",)

    def get_model_perms(self, request):
        return {}


@admin.register(Province)
class ProvinceAdmin(OrgUnitBaseAdmin):
    fields = ("name",)
    list_display = ("name",)
    autocomplete_fields = ()
    write_roles = ()                       # super admins only


@admin.register(County)
class CountyAdmin(OrgUnitBaseAdmin):
    write_roles = ("province_admin",)


@admin.register(Region)
class RegionAdmin(OrgUnitBaseAdmin):
    write_roles = ("province_admin", "county_admin")


@admin.register(School)
class SchoolAdmin(OrgUnitBaseAdmin):
    write_roles = ("province_admin", "county_admin", "region_admin")


@admin.register(User)
class LmsUserAdmin(ScopedAdminMixin, UserAdmin):
    scope_lookup = "org"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("username", "first_name", "last_name", "role", "org", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("username", "first_name", "last_name", "student_code", "national_id")
    autocomplete_fields = ("org",)
    list_select_related = ("org",)
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("اطلاعات شخصی", {"fields": ("first_name", "last_name", "father_name", "national_id", "student_code", "mobile", "email")}),
        ("نقش و واحد سازمانی", {"fields": ("role", "org")}),
        ("وضعیت", {"fields": ("is_active", "is_superuser")}),
        ("زمان‌ها", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": (
        "username", "password1", "password2", "first_name", "last_name", "role", "org")}),)

    # --- privilege-escalation guards: an admin may only manage users strictly below their own rank
    def _lower_roles(self, user):
        return [r for r, k in User.RANK.items() if k > User.RANK[user.role]]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self._priv(request):
            return qs
        return qs.filter(role__in=self._lower_roles(request.user))

    def get_readonly_fields(self, request, obj=None):
        ro = tuple(super().get_readonly_fields(request, obj))
        return ro if self._priv(request) else ro + ("is_superuser",)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "role" and not self._priv(request):
            allowed = set(self._lower_roles(request.user))
            kwargs["choices"] = [c for c in db_field.choices if c[0] in allowed]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        from .models import SchoolProfile, gender_allowed
        if obj.role == "student" and obj.org_id:
            profile = SchoolProfile.objects.select_related("level").filter(school_id=obj.org_id).first()
            if profile and profile.level.code in ("MS2", "KAR", "FNI") and not gender_allowed(obj.gender, profile):
                raise PermissionDenied("جنسیت دانش‌آموز با نوع مدرسه هم‌خوان نیست.")
        if not self._priv(request):
            if User.RANK[obj.role] <= User.RANK[request.user.role] or obj.is_superuser:
                raise PermissionDenied
        super().save_model(request, obj, form, change)      # also verifies the org is inside the editor's scope


@admin.register(ParentLink)
class ParentLinkAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Linking a parent to a student is done by school staff only (the parent's own org should be the child's school)."""
    scope_lookup = "student__org"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("parent", "student", "relation", "is_verified")
    list_filter = ("is_verified",)
    search_fields = ("parent__username", "student__username")
    autocomplete_fields = ("parent", "student")
    list_select_related = ("parent", "student")


@admin.register(TOTPDevice)
class TOTPDeviceAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Super admins can reset (delete) a lost device; secrets are never shown."""
    read_roles = ()
    write_roles = ()
    list_display = ("user", "confirmed", "created_at")
    fields = ("user", "confirmed")
    readonly_fields = ("user", "confirmed")
    search_fields = ("user__username",)

    def has_add_permission(self, request):
        return False


@admin.register(SchoolMembership)
class SchoolMembershipAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Give a principal or teacher access to more schools. Only inside the editor's own scope."""
    scope_lookup = "school"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("user", "school", "created_at")
    search_fields = ("user__username", "school__name")
    autocomplete_fields = ("user", "school")
    list_select_related = ("user", "school")


@admin.register(SchoolProfile)
class SchoolProfileAdmin(ScopedAdminMixin, admin.ModelAdmin):
    scope_lookup = "school"
    read_roles = User.ADMIN_ROLES
    write_roles = User.ADMIN_ROLES
    list_display = ("school", "school_code", "level", "school_type", "shift", "gender")
    list_filter = ("level", "school_type", "shift", "gender")
    search_fields = ("school_code", "school__name")
    autocomplete_fields = ("school", "level")
