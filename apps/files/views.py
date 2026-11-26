import hashlib
import os
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.core.models import audit

from .access import can_download
from .models import FileAsset
from .validators import ALLOWED, check_container, check_header

STUDENT_LIMIT = 25 * 1024 * 1024


def storage_root():
    return Path(getattr(settings, "PRIVATE_MEDIA_ROOT", Path(settings.BASE_DIR) / "private_media"))


def max_bytes(user):
    limit = int(getattr(settings, "UPLOAD_MAX_BYTES", 100 * 1024 * 1024))
    return min(limit, STUDENT_LIMIT) if user.role == User.Role.STUDENT else limit


def _safe_name(name):
    name = os.path.basename(str(name)).replace("\x00", "")
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:255] or "file"


class FileView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "exam"


class UploadView(FileView):
    parser_classes = [MultiPartParser]

    def post(self, request):
        u = request.user
        if u.role == User.Role.PARENT:
            return Response({"detail": "اجازه بارگذاری ندارید."}, status=403)
        f = request.FILES.get("file")
        if f is None:
            return Response({"detail": "فایلی ارسال نشده است."}, status=400)
        name = _safe_name(f.name)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED:
            return Response({"detail": "نوع فایل مجاز نیست. (صوت، فیلم، عکس، PDF، Word، Excel، PowerPoint، zip و rar)"}, status=400)
        if f.size > max_bytes(u):
            return Response({"detail": f"حجم فایل بیش از حد مجاز ({max_bytes(u) // (1024 * 1024)} مگابایت) است."}, status=413)
        head = b""
        for chunk in f.chunks():
            head = chunk[:16]
            break
        if not check_header(ext, head):
            return Response({"detail": "محتوای فایل با نوع آن هم‌خوان نیست."}, status=400)
        stored = f"{uuid.uuid4().hex}.{ext}"
        folder = storage_root() / stored[:2]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / stored
        digest, size = hashlib.sha256(), 0
        with open(path, "wb") as out:
            for chunk in f.chunks():
                size += len(chunk)
                digest.update(chunk)
                out.write(chunk)
        os.chmod(path, 0o600)
        problem = check_container(ext, path)
        if problem:
            path.unlink(missing_ok=True)
            return Response({"detail": problem}, status=400)
        kind, mime, _ = ALLOWED[ext]
        asset = FileAsset.objects.create(owner=u, original_name=name, stored_name=stored, kind=kind, mime=mime,
                                         size=size, sha256=digest.hexdigest())
        audit(request, "file.upload", f"file:{asset.pk}")
        return Response(asset.as_dict(), status=201)


class DownloadView(FileView):
    def get(self, request, pk):
        asset = get_object_or_404(FileAsset, pk=pk)
        if not can_download(request.user, asset):
            raise Http404()                        # same answer as "no such file"
        path = storage_root() / asset.stored_name[:2] / asset.stored_name
        if not path.is_file():
            raise Http404()
        if asset.owner_id != request.user.pk:
            audit(request, "file.download", f"file:{asset.pk}")
        resp = FileResponse(open(path, "rb"), as_attachment=True, filename=asset.original_name,
                            content_type=asset.mime)
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "sandbox; default-src 'none'"
        resp["Cache-Control"] = "private, no-store"
        return resp


class DeleteView(FileView):
    def delete(self, request, pk):
        asset = get_object_or_404(FileAsset, pk=pk, owner=request.user)
        (storage_root() / asset.stored_name[:2] / asset.stored_name).unlink(missing_ok=True)
        asset.delete()
        return Response(status=204)
