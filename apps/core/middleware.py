import re
import uuid

_SAFE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


class RequestIDMiddleware:
    """Attach a request id (for log correlation) and basic privacy headers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.headers.get("X-Request-ID", "")
        request.request_id = rid if _SAFE.match(rid) else uuid.uuid4().hex
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        response.setdefault("Cache-Control", "no-store")   # API responses are per-user
        return response
