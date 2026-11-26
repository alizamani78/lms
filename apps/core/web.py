import secrets

from django.shortcuts import render


def app(request):
    """Single-page client. Inline script/style are allowed only through a per-request CSP nonce."""
    nonce = secrets.token_urlsafe(16)
    resp = render(request, "app.html", {"nonce": nonce})
    resp["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; font-src 'self'; "
        "connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return resp


def system_panel(request):
    """The system administrator's own panel (separate from the Django admin and from the school web app)."""
    nonce = secrets.token_urlsafe(16)
    resp = render(request, "app.html", {"nonce": nonce, "panel": "system"})
    resp["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; font-src 'self'; "
        "connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'")
    return resp
