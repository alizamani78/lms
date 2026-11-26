import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet():
    key = os.environ.get("SMS_ENCRYPTION_KEY") or getattr(settings, "SMS_ENCRYPTION_KEY", "")
    if not key:
        if settings.DEBUG or getattr(settings, "TESTING", False):
            key = base64.urlsafe_b64encode(hashlib.sha256(b"dev-sms:" + settings.SECRET_KEY.encode()).digest()).decode()
        else:
            raise ImproperlyConfigured("SMS_ENCRYPTION_KEY (a Fernet key) is required in production.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_secret(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt_secret(token):
    return _fernet().decrypt(token.encode()).decode() if token else ""


def code_hash(user_id, code):
    """Keyed hash of a one-time code: a leaked database does not reveal codes."""
    return hmac.new(settings.SECRET_KEY.encode(), f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()
