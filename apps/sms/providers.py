"""Generic HTTPS SMS gateway. The school enters the panel's URL, auth header and JSON body template;
placeholders {to}, {sender} and {message} are inserted as JSON-escaped strings. Redirects are refused,
private/loopback targets are refused (same guard as the integrations health check)."""
import json
import urllib.error
import urllib.request

from apps.integrations.health import validate_target

from .crypto import decrypt_secret

MAX_BODY = 4096


class SmsError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def render_body(template, to, sender, message):
    body = template
    for key, value in (("to", to), ("sender", sender), ("message", message)):
        body = body.replace("{" + key + "}", json.dumps(str(value), ensure_ascii=False)[1:-1])
    if len(body) > MAX_BODY:
        raise SmsError("قالب بدنه بیش از حد بزرگ است.")
    json.loads(body)                       # must be valid JSON after substitution
    return body.encode("utf-8")


def send(gateway, to, message):
    """Return (accepted: bool, detail: str). Never raises for provider-side refusals."""
    try:
        validate_target(gateway.base_url)
    except ValueError as e:
        raise SmsError(str(e))
    body = render_body(gateway.body_template, to, gateway.sender, message)
    headers = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json"}
    secret = decrypt_secret(gateway.auth_secret_enc)
    if secret:
        headers[gateway.auth_header or "Authorization"] = secret
    req = urllib.request.Request(gateway.base_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=10) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__
