"""Extension allow-list + content (magic-byte) checks. Nothing else can be uploaded."""
import zipfile

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP = b"PK\x03\x04"
_MKV = b"\x1a\x45\xdf\xa3"


def _sig(*prefixes):
    return lambda h: any(h.startswith(p) for p in prefixes)


def _ftyp(h):
    return h[4:8] == b"ftyp"


def _riff(tag):
    return lambda h: h[:4] == b"RIFF" and h[8:12] == tag


def _mp3(h):
    return h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0)


# extension -> (kind, mime, header check)
ALLOWED = {
    "pdf": ("document", "application/pdf", _sig(b"%PDF")),
    "doc": ("document", "application/msword", _sig(_OLE)),
    "docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", _sig(_ZIP)),
    "xls": ("spreadsheet", "application/vnd.ms-excel", _sig(_OLE)),
    "xlsx": ("spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _sig(_ZIP)),
    "ppt": ("presentation", "application/vnd.ms-powerpoint", _sig(_OLE)),
    "pptx": ("presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation", _sig(_ZIP)),
    "jpg": ("image", "image/jpeg", _sig(b"\xff\xd8\xff")),
    "jpeg": ("image", "image/jpeg", _sig(b"\xff\xd8\xff")),
    "png": ("image", "image/png", _sig(b"\x89PNG\r\n\x1a\n")),
    "gif": ("image", "image/gif", _sig(b"GIF87a", b"GIF89a")),
    "webp": ("image", "image/webp", _riff(b"WEBP")),
    "mp3": ("audio", "audio/mpeg", _mp3),
    "wav": ("audio", "audio/wav", _riff(b"WAVE")),
    "ogg": ("audio", "audio/ogg", _sig(b"OggS")),
    "m4a": ("audio", "audio/mp4", _ftyp),
    "mp4": ("video", "video/mp4", _ftyp),
    "mov": ("video", "video/quicktime", _ftyp),
    "webm": ("video", "video/webm", _sig(_MKV)),
    "mkv": ("video", "video/x-matroska", _sig(_MKV)),
    "zip": ("archive", "application/zip", _sig(_ZIP)),
    "rar": ("archive", "application/vnd.rar", _sig(b"Rar!\x1a\x07")),
}
OOXML_PREFIX = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
DANGEROUS = (".exe", ".bat", ".cmd", ".scr", ".msi", ".com", ".jar", ".js", ".vbs", ".ps1", ".dll", ".lnk", ".hta", ".sh")
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 2 * 1024 ** 3


def check_header(ext, head):
    return ext in ALLOWED and ALLOWED[ext][2](head)


def check_container(ext, path):
    """Deeper checks for zip-based formats. Returns an error message or None. (RAR cannot be inspected here.)"""
    if ext not in ("zip", "docx", "xlsx", "pptx"):
        return None
    if not zipfile.is_zipfile(path):
        return "فایل فشرده/آفیس معتبر نیست."
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_ZIP_UNCOMPRESSED:
            return "محتوای فایل فشرده بیش از حد بزرگ است."
        names = [i.filename for i in infos]
        if ext in OOXML_PREFIX:
            if "[Content_Types].xml" not in names or not any(n.startswith(OOXML_PREFIX[ext]) for n in names):
                return "ساختار فایل آفیس معتبر نیست."
        elif any(n.lower().endswith(DANGEROUS) for n in names):
            return "فایل فشرده شامل فایل اجرایی/اسکریپت است و پذیرفته نمی‌شود."
    return None
