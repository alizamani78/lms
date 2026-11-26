import io

from django.core.files.uploadedfile import SimpleUploadedFile


def png_upload(name="a.png"):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (10, 10), (200, 30, 30)).save(b, "PNG")
    return SimpleUploadedFile(name, b.getvalue())
