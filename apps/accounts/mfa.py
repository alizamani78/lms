"""TOTP (RFC 6238) second factor using only the standard library; secrets encrypted with Fernet."""
import base64
import hashlib
import hmac
import os
import struct
import time
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .models import TOTPDevice, User

ISSUER = "Razavi LMS"


def mfa_required_for(user):
    """An admin role must use two-step sign-in only while it is switched on for that person
    (a confirmed device exists). When the system administrator switches it off, the person signs in
    with the password alone and is not asked to set it up again."""
    enforce = getattr(settings, "MFA_ENFORCE", not (settings.DEBUG or getattr(settings, "TESTING", False)))
    if not (enforce and user.role in User.ADMIN_ROLES):
        return False
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def _fernet():
    from cryptography.fernet import Fernet     # pip install cryptography
    key = os.environ.get("MFA_ENCRYPTION_KEY") or getattr(settings, "MFA_ENCRYPTION_KEY", "")
    if not key:
        if settings.DEBUG or getattr(settings, "TESTING", False):
            key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()
        else:
            raise ImproperlyConfigured("MFA_ENCRYPTION_KEY (a Fernet key) is required in production.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def new_secret():
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def encrypt(secret):
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(device):
    return _fernet().decrypt(device.secret_enc.encode()).decode()


def provisioning_uri(user, secret):
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(user.username)}?secret={secret}"
            f"&issuer={quote(ISSUER)}&digits=6&period=30")


def _hotp(key, counter):
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def get_confirmed_device(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def verify_and_consume(device, code):
    """True once per time-step: a code can never be replayed."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False
    secret = decrypt(device)
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    now, matched = int(time.time()) // 30, None
    for step in (now - 1, now, now + 1):
        if hmac.compare_digest(_hotp(key, step), code):
            matched = step
    if matched is None:
        return False
    return TOTPDevice.objects.filter(pk=device.pk, last_step__lt=matched).update(last_step=matched) == 1
