import multiprocessing
import os

# Behind nginx: listen on a unix socket owned by the service user. Override with GUNICORN_BIND for local testing.
bind = os.environ.get("GUNICORN_BIND", "unix:/run/razavi-lms/gunicorn.sock")
workers = int(os.environ.get("WEB_CONCURRENCY", max(2, multiprocessing.cpu_count())))
worker_class = "gthread"
threads = 4
timeout = 30
graceful_timeout = 30
keepalive = 5
max_requests = 2000
max_requests_jitter = 200
forwarded_allow_ips = "127.0.0.1"
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
accesslog = "-"
errorlog = "-"
