from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Liveness: no dependencies, used by the load balancer."""
    return JsonResponse({"ok": True})


def readyz(request):
    """Readiness: DB + cache reachable."""
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
        cache.set("readyz", 1, 5)
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False}, status=503)
