import multiprocessing
import os

bind = f"0.0.0.0:{os.getenv('PORT', '5000')}"

# gthread workers — SSE streams hold a request thread for minutes, so we need
# threads, not async. The app already does its own parallel I/O inside the
# handler via ThreadPoolExecutor, so each gthread serves 1 SSE stream.
worker_class = "gthread"
workers = int(os.getenv("WEB_CONCURRENCY", max(2, multiprocessing.cpu_count())))
threads = int(os.getenv("WEB_THREADS", "8"))

# Match the in-app 60-minute SSE deadline so gunicorn doesn't kill long fetches.
timeout = 3600
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")

forwarded_allow_ips = "*"
proxy_allow_ips = "*"
