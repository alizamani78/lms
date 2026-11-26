"""Provider adapters. Media never touches Django; we mint join URLs and (optionally) create rooms."""
import hashlib
import xml.etree.ElementTree as ElementTreeParse
import urllib.error
import urllib.request
from urllib.parse import urlencode

from django.conf import settings

from apps.integrations.health import validate_target


class TransientProviderError(Exception):
    """Network/5xx problem: worth retrying."""


class PermanentProviderError(Exception):
    """The provider rejected the request: retrying will not help."""


class Provider:
    def join_url(self, session, user, moderator):
        base, secret = _credentials(session)
        if not (base and secret):
            raise RuntimeError("BBB is not configured")
        return self._signed("join", {
            "fullName": (user.get_full_name() or user.username)[:60], "meetingID": session.external_id,
            "role": "MODERATOR" if moderator else "VIEWER", "userID": str(user.pk), "redirect": "true",
            # رابط بیگ‌بلو به زبان فارسی باشد
            "userdata-bbb_override_default_locale": "fa"},
            base, secret)

    def is_running(self, session):
        """آیا جلسه در سرور بیگ‌بلو شروع شده است؟ (تا معلم وارد نشده، دانش‌آموز وارد نمی‌شود)"""
        base, secret = _credentials(session)
        if not (base and secret):
            raise RuntimeError("BBB is not configured")
        url = self._signed("isMeetingRunning", {"meetingID": session.external_id}, base, secret)
        try:
            root = ElementTreeParse.fromstring(_http_get(url))
        except Exception:
            raise RuntimeError("bad response")
        return (root.findtext("running") or "").strip().lower() == "true"

    def create_meeting(self, session):
        raise NotImplementedError


def _credentials(session):
    """Prefer the admin-managed ExternalSystem; fall back to environment settings."""
    system = session.system
    if system is not None:
        if not system.is_active:
            raise RuntimeError("system inactive")
        return system.base_url, system.secret()
    return settings.BBB_URL, settings.BBB_SECRET


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_get(url, timeout=10, limit=1_000_000):
    try:
        validate_target(url)
    except ValueError as e:
        raise PermanentProviderError(str(e))
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "RazaviLMS"}), timeout=timeout) as r:
            return r.read(limit)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise TransientProviderError(f"HTTP {e.code}")
        raise PermanentProviderError(f"HTTP {e.code}")
    except Exception as e:
        raise TransientProviderError(type(e).__name__)


class BigBlueButton(Provider):
    def _signed(self, call, params, base, secret):
        query = urlencode(params)
        checksum = hashlib.sha256(f"{call}{query}{secret}".encode()).hexdigest()
        return f"{base.rstrip('/')}/api/{call}?{query}&checksum={checksum}"

    # نقش‌هایی که کنترل کامل جلسه را دارند (دبیر، مدیر، معاون و مدیر سیستم)
    STAFF_ROLES = {"teacher", "super_admin", "province_admin", "county_admin", "region_admin",
                   "school_admin", "school_deputy_edu", "school_deputy_exec", "school_deputy_culture", "expert"}

    def join_url(self, session, user, moderator):
        base, secret = _credentials(session)
        if not (base and secret):
            raise RuntimeError("BBB is not configured")
        staff = user.role in self.STAFF_ROLES and bool(moderator)
        params = {
            "fullName": (user.get_full_name() or user.username)[:60], "meetingID": session.external_id,
            "role": "MODERATOR" if staff else "VIEWER", "userID": str(user.pk), "redirect": "true",
            # زبان پیش‌فرض رابط BigBlueButton فارسی است
            "userdata-bbb_override_default_locale": "fa",
        }
        if not staff:
            # دانش‌آموز: فقط شنیدن و دیدن؛ بدون دوربین و میکروفون و بدون پیش‌نمایش ویدیو
            params.update({"userdata-bbb_listen_only_mode": "true", "userdata-bbb_skip_video_preview": "true",
                           "userdata-bbb_auto_share_webcam": "false", "userdata-bbb_skip_check_audio": "true"})
        return self._signed("join", params, base, secret)

    def create_meeting(self, session):
        """BBB `create` is idempotent for the same meetingID, so a retry never produces a duplicate room."""
        from defusedxml import ElementTree        # pip install defusedxml
        base, secret = _credentials(session)
        if not (base and secret):
            raise PermanentProviderError("BBB is not configured")
        minutes = max(int((session.ends_at - session.starts_at).total_seconds() // 60), 1)
        # دسترسی دانش‌آموزان طبق تنظیم همان جلسه؛ دبیر و مدیر همیشه همه‌ی امکانات را دارند
        url = self._signed("create", {"name": session.title[:100], "meetingID": session.external_id,
                                      "duration": minutes,
                                      "record": "true" if session.can("record") else "false",
                                      "allowStartStopRecording": "true",
                                      "lockSettingsDisableMic": "false" if session.can("mic") else "true",
                                      "lockSettingsDisableCam": "false" if session.can("cam") else "true",
                                      "lockSettingsDisablePublicChat": "false" if session.can("chat") else "true",
                                      "lockSettingsDisablePrivateChat": "false" if session.can("chat") else "true",
                                      "lockSettingsDisableNote": "false" if session.can("file") else "true",
                                      # تصویر دوربین فقط ناظران دیده می‌شود
                                      "webcamsOnlyForModerator": "true",
                                      # دانش‌آموزان تا معلم یا مدیر نیامده‌اند وارد نمی‌شوند؛ ورود هر دانش‌آموز با تأیید ناظر
                                      "guestPolicy": "ASK_MODERATOR"}, base, secret)
        try:
            root = ElementTree.fromstring(_http_get(url))
        except (ElementTree.ParseError, ValueError):
            raise TransientProviderError("bad response")
        if (root.findtext("returncode") or "") != "SUCCESS":
            raise PermanentProviderError((root.findtext("messageKey") or "FAILED")[:100])


PROVIDERS = {"bbb": BigBlueButton()}   # register Adobe Connect / Skyroom adapters here


class AdobeConnect(Provider):
    """Adobe Connect: ساخت جلسه، ورود مدیر (با حساب API مدرسه) و ورود دانش‌آموز به‌عنوان مهمان."""

    def _system(self, session):
        from . import adobe
        system = session.system
        if system is None or not system.is_active:
            raise RuntimeError("سامانه‌ی Adobe Connect این مدرسه فعال نیست.")
        return adobe, system

    def _url_path(self, session):
        return f"lms{session.pk}"

    def create_meeting(self, session):
        adobe, system = self._system(session)
        cookie = adobe.login(system.base_url, system.admin_user, system.get_admin_password())
        sco_id, _ = adobe.create_meeting(system.base_url, cookie, session.title or f"کلاس {session.pk}", self._url_path(session))
        session.external_id = sco_id
        session.save(update_fields=["external_id"])
        return sco_id

    def account_for(self, system, user):
        """حساب Adobe کاربر در این مدرسه؛ اگر نبود، ساخته می‌شود (و در Adobe Connect هم ساخته می‌شود)."""
        import secrets
        from apps.integrations.models import AdobeAccount
        from . import adobe
        acc = AdobeAccount.objects.filter(system=system, user=user).first()
        if acc is not None and acc.principal_id and acc.get_password():
            return acc
        login_name = f"{user.username}@lms.local"
        password = secrets.token_urlsafe(12) + "A1!"
        admin = adobe.login(system.base_url, system.admin_user, system.get_admin_password())
        pid = adobe.ensure_principal(system.base_url, admin, login_name, password,
                                     user.first_name, user.last_name)
        acc = acc or AdobeAccount(system=system, user=user)
        acc.login, acc.principal_id = login_name, pid
        acc.set_password(password)
        acc.save()
        return acc

    def join_url(self, session, user, moderator):
        adobe, system = self._system(session)
        if not session.external_id:
            raise RuntimeError("این جلسه هنوز در Adobe Connect ساخته نشده است.")
        acc = self.account_for(system, user)
        role = "host" if (moderator or user.role != "student") else "view"
        admin = adobe.login(system.base_url, system.admin_user, system.get_admin_password())
        adobe.grant(system.base_url, admin, session.external_id, acc.principal_id, role)
        cookie = adobe.user_login(system.base_url, acc.login, acc.get_password())
        return adobe.join_url(system.base_url, self._url_path(session), cookie=cookie, host_user=True)

    def is_running(self, session):
        return bool(session.external_id)


PROVIDERS["adobe"] = AdobeConnect()
