from django.contrib import admin
from django.utils import timezone

from apps.core.admin_base import ScopedAdminMixin

from .health import check
from .models import ExternalSystem


@admin.register(ExternalSystem)
class ExternalSystemAdmin(ScopedAdminMixin, admin.ModelAdmin):
    """Super admins only (read_roles/write_roles empty)."""
    read_roles = ()
    write_roles = ()
    list_display = ("name", "kind", "base_url", "is_active", "secret_set", "last_check_ok", "last_check_at")
    list_filter = ("kind", "is_active")
    search_fields = ("name",)
    readonly_fields = ("secret_set", "last_check_at", "last_check_ok", "last_check_detail")
    actions = ["check_connection"]

    @admin.display(boolean=True, description="راز روی سرور تنظیم شده")
    def secret_set(self, obj):
        return obj.secret_configured() if obj.pk else None

    @admin.action(description="بررسی اتصال به سامانه‌های انتخاب‌شده", permissions=["change"])
    def check_connection(self, request, queryset):
        for s in queryset:
            ok, detail = check(s)
            s.last_check_at, s.last_check_ok, s.last_check_detail = timezone.now(), ok, detail[:200]
            s.save(update_fields=["last_check_at", "last_check_ok", "last_check_detail"])
            self.message_user(request, f"{s.name}: {'موفق' if ok else 'ناموفق'} — {detail}",
                              level=25 if ok else 40)
