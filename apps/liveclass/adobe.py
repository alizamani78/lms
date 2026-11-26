"""Adobe Connect (کلاس آنلاین): ورود مدیر با حساب API، ساخت جلسه (sco)، آدرس ورود، و خواندن حضور دانش‌آموزان.
تمام درخواست‌ها از API XML رسمی Adobe Connect به شکل  <host>/api/xml?action=…  انجام می‌شود.
نکته: ساختار پاسخ‌ها طبق مستندات Adobe Connect نوشته شده؛ قبل از استفاده‌ی عملی، «تست ارتباط» را روی سرور واقعی بزنید."""
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from apps.integrations.health import validate_target

from .providers import PermanentProviderError, TransientProviderError


def _base(host):
    h = (host or "").strip().rstrip("/")
    if not h.startswith("http"):
        h = "https://" + h
    return h


def call(host, params, cookie=None, timeout=15):
    """یک فراخوانی API. برمی‌گرداند (root, cookie) — cookie همان BREEZESESSION پس از login است."""
    base = _base(host)
    url = f"{base}/api/xml?{urlencode(params)}"
    try:
        validate_target(url)
    except ValueError as e:
        raise PermanentProviderError(str(e))
    req = urllib.request.Request(url, headers={"User-Agent": "RazaviLMS"})
    if cookie:
        req.add_header("Cookie", f"BREEZESESSION={cookie}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2_000_000)
            new_cookie = cookie
            for c in r.headers.get_all("Set-Cookie") or []:
                if c.startswith("BREEZESESSION="):
                    new_cookie = c.split(";", 1)[0].split("=", 1)[1]
    except urllib.error.HTTPError as e:
        raise TransientProviderError(f"HTTP {e.code}") if e.code >= 500 else PermanentProviderError(f"HTTP {e.code}")
    except Exception as e:  # network problems are worth retrying
        raise TransientProviderError(type(e).__name__)
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise PermanentProviderError("پاسخ نامعتبر از Adobe Connect")
    status = root.find("status")
    if status is not None and status.get("code") not in (None, "ok"):
        sub = status.get("subcode", "")
        raise PermanentProviderError(f"Adobe Connect: {status.get('code')} {sub}".strip())
    return root, new_cookie


def login(host, user, password):
    """ورود با حساب مدیر API؛ برمی‌گرداند توکن BREEZESESSION."""
    if not (host and user and password):
        raise PermanentProviderError("هاست، نام کاربری یا رمز مدیر تنظیم نشده است.")
    _, cookie = call(host, {"action": "login", "login": user, "password": password})
    if not cookie:
        raise PermanentProviderError("ورود به Adobe Connect انجام نشد (نام کاربری یا رمز را بررسی کنید).")
    return cookie


def my_meetings_folder(host, cookie):
    root, _ = call(host, {"action": "sco-shortcuts"}, cookie=cookie)
    for sc in root.iter("sco"):
        if sc.get("type") == "user-meetings":
            return sc.get("sco-id")
    raise PermanentProviderError("پوشه‌ی جلسات مدیر در Adobe Connect پیدا نشد.")


def create_meeting(host, cookie, name, url_path):
    folder = my_meetings_folder(host, cookie)
    root, _ = call(host, {"action": "sco-update", "type": "meeting", "name": name[:200],
                          "folder-id": folder, "url-path": url_path}, cookie=cookie)
    sco = root.find(".//sco")
    if sco is None or not sco.get("sco-id"):
        raise PermanentProviderError("جلسه در Adobe Connect ساخته نشد.")
    # the room is open to view for guests, so students join with a name only
    call(host, {"action": "permissions-update", "acl-id": sco.get("sco-id"), "principal-id": "public-access",
                "permission-id": "view"}, cookie=cookie)
    return sco.get("sco-id"), sco.get("url-path")


def join_url(host, url_path, cookie=None, host_user=False):
    """مدیر با توکن خودش وارد می‌شود؛ دانش‌آموز به‌عنوان مهمان با نام خودش."""
    base = f"{_base(host)}/{url_path.strip('/')}/"
    if host_user and cookie:
        return f"{base}?session={cookie}"
    return base


def attendance_rows(host, cookie, sco_id):
    """حضور هر کاربر: (نام کاربری، ثانیه‌های حضور). ساختار سطر طبق report-meeting-attendance."""
    root, _ = call(host, {"action": "report-meeting-attendance", "sco-id": sco_id}, cookie=cookie)
    out = []
    for row in root.iter("row"):
        login_name = row.get("login") or (row.findtext("login") or "")
        if not login_name:
            continue
        seconds = row.get("duration-seconds") or ""
        if seconds.isdigit():
            secs = int(seconds)
        else:
            secs = 0
        out.append((login_name.strip(), secs))
    return out


def test_connection(host, user, password):
    """دکمه‌ی تست ارتباط: ورود با حساب مدیر و خواندن اطلاعات پوشه‌ی جلسات."""
    cookie = login(host, user, password)
    my_meetings_folder(host, cookie)
    return "ورود به Adobe Connect موفق بود و پوشه‌ی جلسات مدیر پیدا شد."


def ensure_principal(host, admin_cookie, login_name, password, first, last):
    """حساب کاربر در Adobe Connect؛ اگر وجود داشته باشد، شناسه‌اش برگردانده می‌شود."""
    root, _ = call(host, {"action": "principal-update", "login": login_name, "password": password,
                          "first-name": first[:60] or login_name, "last-name": last[:60] or login_name,
                          "type": "user", "has-children": "0", "send-email": "false"}, cookie=admin_cookie)
    p = root.find(".//principal")
    if p is None or not p.get("principal-id"):
        raise PermanentProviderError("حساب کاربر در Adobe Connect ساخته نشد.")
    return p.get("principal-id")


def grant(host, admin_cookie, sco_id, principal_id, role):
    """نقش روی جلسه: host (دبیر)، view (دانش‌آموز)."""
    call(host, {"action": "permissions-update", "acl-id": sco_id, "principal-id": principal_id,
                "permission-id": role}, cookie=admin_cookie)


def user_login(host, login_name, password):
    """ورود با حساب خود کاربر؛ توکن BREEZESESSION او را برمی‌گرداند (برای ورود به جلسه با نقش خودش)."""
    _, cookie = call(host, {"action": "login", "login": login_name, "password": password})
    if not cookie:
        raise PermanentProviderError("ورود کاربر به Adobe Connect انجام نشد.")
    return cookie

