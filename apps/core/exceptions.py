import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

log = logging.getLogger("lms.errors")


def safe_exception_handler(exc, context):
    """Never leak internals: unknown errors become a generic 500 with only a request id."""
    response = exception_handler(exc, context)
    if response is not None:
        return response
    request = context.get("request")
    rid = getattr(getattr(request, "_request", None), "request_id", "-")
    log.exception("unhandled error rid=%s", rid)
    return Response({"detail": "خطای داخلی سرور.", "request_id": rid}, status=500)
