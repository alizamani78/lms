from django.conf import settings
from django.db import models


class FileAsset(models.Model):
    """An uploaded file kept in PRIVATE storage (never under a public URL). Access is decided per request."""
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    original_name = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=80, unique=True)
    kind = models.CharField(max_length=12)
    mime = models.CharField(max_length=80)
    size = models.BigIntegerField()
    sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "فایل"
        verbose_name_plural = "فایل‌های بارگذاری‌شده"
        indexes = [models.Index(fields=["owner", "created_at"])]

    def __str__(self):
        return self.original_name

    def as_dict(self):
        return {"id": self.pk, "name": self.original_name, "size": self.size, "kind": self.kind}
