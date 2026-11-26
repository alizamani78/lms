"""Connectivity check used by the admin action. Guarded against SSRF: https only (http in DEBUG), loopback /
link-local (cloud metadata) / multicast / reserved targets are refused, redirects are not followed."""
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

from django.conf import settings


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def validate_target(url):
    p = urlparse(url)
    allowed = ("https", "http") if settings.DEBUG else ("https",)
    if p.scheme not in allowed or not p.hostname:
        raise ValueError("آدرس باید با https شروع شود.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError("نام میزبان پیدا نشد.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise ValueError("این آدرس مجاز نیست.")
    return p


def check(system):
    """Return (ok, detail). 'ok' means the server answered (any status below 500)."""
    try:
        validate_target(system.base_url)
    except ValueError as e:
        return False, str(e)
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(system.base_url, headers={"User-Agent": "RazaviLMS-healthcheck"})
    try:
        with opener.open(req, timeout=5) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return e.code < 500, f"HTTP {e.code}"
    except Exception as e:                      # timeouts, TLS errors, refused connections ...
        return False, type(e).__name__
