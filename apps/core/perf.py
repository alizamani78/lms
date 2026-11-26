"""Request timing and resource monitor (system administrator only).

Timing: every request adds one count and its duration to a per-minute bucket in the shared cache (so all gunicorn
workers contribute). Requests slower than SLOW_MS are listed separately. Paths are normalised (ids become {id})."""
import os
import re
import time

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

SLOW_MS = int(os.environ.get("SLOW_REQUEST_MS", "800"))
KEEP_MINUTES = 60
_ID = re.compile(r"/\d+(?=/|$)")


def norm_path(path):
    return _ID.sub("/{id}", path)[:120]


def record(path, status, ms):
    minute = int(time.time() // 60)
    base = f"perf:{minute}"
    cache.add(base + ":n", 0, 3600 * 2)
    cache.add(base + ":ms", 0, 3600 * 2)
    cache.add(base + ":slow", 0, 3600 * 2)
    try:
        cache.incr(base + ":n")
    except ValueError:
        cache.set(base + ":n", 1, 3600 * 2)
    cache.set(base + ":ms", (cache.get(base + ":ms") or 0) + ms, 3600 * 2)
    if ms >= SLOW_MS:
        cache.set(base + ":slow", (cache.get(base + ":slow") or 0) + 1, 3600 * 2)
        slow = cache.get("perf:slowlist") or []
        slow.append({"at": timezone.now().isoformat(), "path": norm_path(path), "status": status, "ms": round(ms)})
        cache.set("perf:slowlist", slow[-200:], 3600 * 2)


class TimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        t0 = time.perf_counter()
        response = self.get_response(request)
        ms = (time.perf_counter() - t0) * 1000
        if request.path.startswith("/api/") or request.path == "/":
            try:
                record(request.path, response.status_code, ms)
            except Exception:
                pass                                         # monitoring must never break a request
        return response


def snapshot():
    out = {"at": timezone.now().isoformat()}
    try:
        import psutil
        out["cpu_percent"] = psutil.cpu_percent(interval=0.2)
        out["cpu_count"] = psutil.cpu_count()
        vm = psutil.virtual_memory()
        out["ram"] = {"total": vm.total, "used": vm.used, "percent": vm.percent, "available": vm.available}
        sw = psutil.swap_memory()
        out["swap"] = {"total": sw.total, "used": sw.used, "percent": sw.percent}
        du = psutil.disk_usage(str(settings.BASE_DIR))
        out["disk"] = {"total": du.total, "used": du.used, "percent": du.percent}
        proc = psutil.Process(os.getpid())
        out["process"] = {"rss": proc.memory_info().rss, "threads": proc.num_threads()}
    except ImportError:
        out["cpu_percent"] = None
        out["note"] = "psutil نصب نیست؛ pip install psutil را اجرا کنید."
        try:
            out["load_avg"] = os.getloadavg()
        except (AttributeError, OSError):
            pass
    try:
        out["load_avg"] = os.getloadavg()
    except (AttributeError, OSError):
        pass
    t0 = time.perf_counter()
    with connection.cursor() as c:
        c.execute("SELECT 1")
    out["db"] = {"vendor": connection.vendor, "ping_ms": round((time.perf_counter() - t0) * 1000, 2)}
    out["cache"] = cache_stats()
    out["queues"] = queue_lengths()
    out["requests"] = request_stats()
    return out


def cache_stats():
    backend = settings.CACHES["default"]["BACKEND"]
    if "redis" not in backend:
        return {"backend": backend.rsplit(".", 1)[-1], "note": "حافظه نهان داخل فرایند است (آزمایشی)."}
    try:
        client = cache._cache.get_client(write=False)     # Django's RedisCache exposes its client
        info = client.info()
        hits, misses = info.get("keyspace_hits", 0), info.get("keyspace_misses", 0)
        return {"backend": "redis", "used_memory": info.get("used_memory"), "max_memory": info.get("maxmemory"),
                "clients": info.get("connected_clients"), "ops_per_sec": info.get("instantaneous_ops_per_sec"),
                "hit_rate": round(100 * hits / (hits + misses), 1) if hits + misses else None,
                "keys": client.dbsize()}
    except Exception as e:
        return {"backend": "redis", "error": type(e).__name__}


def queue_lengths():
    """Celery queues (default, grading) on the broker; a growing queue means workers cannot keep up."""
    broker = getattr(settings, "CELERY_BROKER_URL", "")
    if not broker.startswith("redis"):
        return {}
    try:
        import redis
        r = redis.Redis.from_url(broker, socket_timeout=1)
        return {q: r.llen(q) for q in ("default", "grading")}
    except Exception as e:
        return {"error": type(e).__name__}


def request_stats(minutes=15):
    now = int(time.time() // 60)
    n = ms = slow = 0
    per_min = []
    for m in range(now - minutes + 1, now + 1):
        c = cache.get(f"perf:{m}:n") or 0
        t = cache.get(f"perf:{m}:ms") or 0
        s = cache.get(f"perf:{m}:slow") or 0
        n, ms, slow = n + c, ms + t, slow + s
        per_min.append({"minute": m, "requests": c, "avg_ms": round(t / c) if c else 0})
    return {"window_minutes": minutes, "requests": n, "avg_ms": round(ms / n) if n else 0, "slow": slow,
            "slow_threshold_ms": SLOW_MS, "per_minute": per_min[-15:],
            "recent_slow": (cache.get("perf:slowlist") or [])[-30:][::-1]}
