from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Append-only trail of security-relevant actions."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60, db_index=True)
    target = models.CharField(max_length=80, blank=True)
    ip = models.GenericIPAddressField(null=True)
    request_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]


def client_ip(request):
    # Only trust the first hop your own load balancer appends; configure the LB to overwrite XFF.
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (xff.split(",")[0].strip() or None) if xff else request.META.get("REMOTE_ADDR")


def audit(request, action, target=""):
    AuditLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        action=action,
        target=str(target)[:80],
        ip=client_ip(request),
        request_id=getattr(request, "request_id", ""),
    )


class GradeChange(models.Model):
    """Append-only history of every grade given or changed (essay answers, assignment submissions)."""
    kind = models.CharField(max_length=20)                 # "answer" | "submission"
    ref_id = models.BigIntegerField()
    student = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    old_points = models.DecimalField(max_digits=7, decimal_places=2, null=True)
    new_points = models.DecimalField(max_digits=7, decimal_places=2, null=True)
    reason = models.CharField(max_length=300, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.GenericIPAddressField(null=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "تاریخچه تغییر نمره"
        verbose_name_plural = "تاریخچه تغییر نمره‌ها"
        indexes = [models.Index(fields=["kind", "ref_id"]), models.Index(fields=["student", "changed_at"])]


def record_grade_change(request, kind, ref_id, student_id, old, new, reason=""):
    GradeChange.objects.create(kind=kind, ref_id=ref_id, student_id=student_id, old_points=old, new_points=new,
                               reason=reason[:300], changed_by=request.user, ip=client_ip(request),
                               request_id=getattr(request, "request_id", ""))
