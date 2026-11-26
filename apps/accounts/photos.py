"""Profile photos: processed (re-encoded, resized, EXIF removed) and kept in PRIVATE storage.
Served only through the access-checked endpoint GET /api/users/<id>/photo/."""
import hashlib
import io
import os
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import audit
from apps.core.scope import kartabl_filter, org_in_scope, scope_q
from apps.files.models import FileAsset
from apps.files.views import storage_root

from .models import ParentLink, User

MAX_PHOTO_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_ZIP_ENTRIES = 3000
MAX_ZIP_TOTAL = 400 * 1024 * 1024
ALLOWED_EXT = {"jpg", "jpeg", "png"}


def process_image(data):
    """Return a 256px JPEG (RGB, no metadata). Raises ValueError for anything that is not a clean image."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > MAX_PIXELS:
                raise ValueError("ابعاد تصویر بیش از حد بزرگ است.")
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((256, 256))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError("فایل تصویر معتبر نیست.") from e


def store_photo(uploader, target, jpeg):
    stored = f"{uuid.uuid4().hex}.jpg"
    folder = storage_root() / stored[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / stored
    path.write_bytes(jpeg)
    os.chmod(path, 0o600)
    asset = FileAsset.objects.create(owner=uploader, original_name=f"photo-{target.pk}.jpg", stored_name=stored,
                                     kind="image", mime="image/jpeg", size=len(jpeg),
                                     sha256=hashlib.sha256(jpeg).hexdigest())
    old = target.photo
    target.photo = asset
    target.save(update_fields=["photo"])
    if old is not None:
        (storage_root() / old.stored_name[:2] / old.stored_name).unlink(missing_ok=True)
        old.delete()
    return asset


def can_view_photo(user, target):
    if user.pk == target.pk or user.role == User.Role.SUPER_ADMIN:
        return True
    if user.role in User.ADMIN_ROLES:
        return target.org is not None and org_in_scope(user, target.org)
    if user.role == User.Role.TEACHER:
        from apps.courses.models import Enrollment
        return Enrollment.objects.filter(student=target, course__teacher=user).exists()
    if user.role == User.Role.PARENT:
        return ParentLink.objects.filter(parent=user, student=target, is_verified=True).exists()
    return False


class PhotoView(APIView):
    """GET (access-checked) and PUT (self, or admins inside their scope) for one user's photo."""

    def get(self, request, pk):
        target = get_object_or_404(User.objects.select_related("photo"), pk=pk)
        if target.photo_id is None or not can_view_photo(request.user, target):
            raise Http404()
        path = storage_root() / target.photo.stored_name[:2] / target.photo.stored_name
        if not path.is_file():
            raise Http404()
        resp = FileResponse(open(path, "rb"), content_type="image/jpeg")
        resp["Cache-Control"] = "private, no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp

    def put(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        u = request.user
        if u.pk != target.pk:
            if u.role not in User.ADMIN_ROLES or (target.org is None or not org_in_scope(u, target.org)):
                raise Http404()
        f = request.FILES.get("file")
        if f is None or f.size > MAX_PHOTO_BYTES:
            raise serializers.ValidationError({"file": "فایل تصویر (حداکثر ۳ مگابایت) لازم است."})
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ALLOWED_EXT:
            raise serializers.ValidationError({"file": "فقط JPG یا PNG."})
        try:
            jpeg = process_image(f.read())
        except ValueError as e:
            raise serializers.ValidationError({"file": str(e)})
        asset = store_photo(u, target, jpeg)
        audit(request, "photo.set", f"user:{target.pk}")
        return Response({"id": target.pk, "photo": asset.pk})


class PhotoZipView(APIView):
    """POST /api/photos/zip/?unit=<province | region | school id>  (multipart: file)

    One zip updates MANY students of that unit:
      * image files named by national id (or student code): 970487177.jpg -> profile photo
      * one data file (.csv or .xlsx; any name) with a «کد ملی» column and any of: نام، نام خانوادگی، نام پدر، موبایل،
        جنسیت، کد دانش‌آموز -> updates those students' details
    Allowed: admins inside their scope and experts whose unit covers the chosen unit. Every student is matched ONLY inside
    the chosen unit, every value is re-validated, and the answer lists what was saved, unmatched and invalid."""
    parser_classes = []                      # replaced below (multipart)

    def post(self, request):
        from apps.core.persian import is_valid_national_id, normalize_text
        from apps.imports.parsers import parse as parse_table
        u = request.user
        if u.role not in User.ADMIN_ROLES and u.role != User.Role.EXPERT:
            raise PermissionDenied()
        raw = request.query_params.get("unit") or request.query_params.get("school") or request.data.get("unit") or request.data.get("school")
        from apps.accounts.models import OrgUnit
        unit = OrgUnit.objects.filter(pk=raw).first() if str(raw or "").isdigit() else None
        if unit is None or not org_in_scope(u, unit):
            raise Http404()
        f = request.FILES.get("file")
        if f is None or f.size > 200 * 1024 * 1024:
            raise serializers.ValidationError({"file": "فایل zip (حداکثر ۲۰۰ مگابایت) لازم است."})
        try:
            zf = zipfile.ZipFile(io.BytesIO(f.read()))
        except zipfile.BadZipFile:
            raise serializers.ValidationError({"file": "فایل zip معتبر نیست."})
        report = {"photos_saved": 0, "details_updated": 0, "unmatched": [], "invalid": []}
        with zf:
            infos = [i for i in zf.infolist() if not i.is_dir() and "__MACOSX" not in i.filename]
            if len(infos) > 20000 or sum(i.file_size for i in infos) > 1024 ** 3:
                raise serializers.ValidationError({"file": "محتوای zip بیش از حد بزرگ است."})
            images, tables = {}, []
            for info in infos:
                name = info.filename.rsplit("/", 1)[-1]
                key, _, ext = name.rpartition(".")
                ext = ext.lower()
                if ext in ALLOWED_EXT:
                    if info.file_size > MAX_PHOTO_BYTES:
                        report["invalid"].append(name)
                    else:
                        images[normalize_text(key)] = info
                elif ext in ("csv", "xlsx"):
                    tables.append(info)
            scope_students = User.objects.filter(role="student", org__path__startswith=unit.path)

            def lookup(keys):
                found = {}
                for s in scope_students.filter(Q(national_id__in=keys) | Q(student_code__in=keys)):
                    for k in (s.national_id, s.student_code):
                        if k:
                            found[k] = s
                return found
            # ---- details from the data file
            for info in tables[:1]:
                try:
                    rows, _unknown = parse_table("student_update", name=info.filename, data=zf.read(info))
                except ValueError as e:
                    report["invalid"].append(f"{info.filename}: {e}")
                    rows = []
                keys = {r["values"].get("national_id") for r in rows} - {"", None}
                students = lookup(keys)
                for r in rows:
                    v = r["values"]
                    nid = v.get("national_id", "")
                    if not is_valid_national_id(nid):
                        report["invalid"].append(f"ردیف {r['n']}: کد ملی نامعتبر")
                        continue
                    s = students.get(nid)
                    if s is None:
                        report["unmatched"].append(f"ردیف {r['n']}: {nid}")
                        continue
                    changed = []
                    for fld in ("first_name", "last_name", "father_name"):
                        if v.get(fld) and getattr(s, fld) != v[fld][:150]:
                            setattr(s, fld, v[fld][:150])
                            changed.append(fld)
                    if v.get("mobile"):
                        mob = v["mobile"]
                        if mob.isdigit() and mob.startswith("09") and len(mob) == 11:
                            s.mobile = mob
                            changed.append("mobile")
                        else:
                            report["invalid"].append(f"ردیف {r['n']}: موبایل نامعتبر")
                    if v.get("gender"):
                        g = {"پسر": "boy", "دختر": "girl", "boy": "boy", "girl": "girl"}.get(v["gender"])
                        if g:
                            s.gender = g
                            changed.append("gender")
                    if changed:
                        s.save(update_fields=changed)
                        report["details_updated"] += 1
            # ---- photos
            def padded(k):
                # کد کوتاه‌تر از ۱۰ رقم (بدون صفرهای ابتدایی) را با صفر تکمیل می‌کند تا با کد سامانه مطابقت کند
                return k.zfill(10) if k.isdigit() and len(k) < 10 else k
            students = lookup({k for key in images for k in (key, padded(key))})
            for key, info in images.items():
                target = students.get(key) or students.get(padded(key))
                if target is None:
                    report["unmatched"].append(key)
                    continue
                try:
                    jpeg = process_image(zf.read(info))
                except ValueError:
                    report["invalid"].append(info.filename.rsplit("/", 1)[-1])
                    continue
                store_photo(u, target, jpeg)
                report["photos_saved"] += 1
        audit(request, "photo.zip", f"unit:{unit.pk} photos={report['photos_saved']} details={report['details_updated']}")
        return Response({**report, "unmatched": report["unmatched"][:300], "invalid": report["invalid"][:300]})


from rest_framework.parsers import MultiPartParser as _MP  # noqa: E402
PhotoZipView.parser_classes = [_MP]
