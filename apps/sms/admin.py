from django.contrib import admin

from apps.accounts.models import User
from apps.core.admin_base import ScopedAdminMixin

from .models import PasswordResetCode, SmsGateway, SmsLog


@admin.register(SmsGateway)
class SmsGatewayAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Secrets are never shown: the field is read-only and the API is the only way to set it."""
    scope_lookup = "school"
    read_roles = User.ADMIN_ROLES
    write_roles = ()
    list_display = ("name", "school", "is_active", "last_test_ok", "last_test_at")
    exclude = ("auth_secret_enc",)
    readonly_fields = ("last_test_at", "last_test_ok")

    def has_add_permission(self, request):
        return False


@admin.register(SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "gateway", "to_masked", "purpose", "accepted", "detail")
    list_filter = ("purpose", "accepted")
    readonly_fields = [f.name for f in SmsLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    """Read-only audit of codes (hashes only); superusers."""
    list_display = ("user", "created_at", "expires_at", "attempts", "used_at")
    readonly_fields = [f.name for f in PasswordResetCode._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_module_permission(self, request):
        return request.user.is_superuser
