"""
Production Gunicorn Configuration for Enterprise IT Support Copilot
────────────────────────────────────────────────────────────────────
Optimized for high-concurrency production deployments behind AWS ALB.
Calculates workers dynamically based on CPU core count or WEB_CONCURRENCY.
"""

import multiprocessing
import os

# Server socket
bind = os.getenv("BIND_ADDRESS", "0.0.0.0:8080")
backlog = int(os.getenv("GUNICORN_BACKLOG", "2048"))

# Worker processes
# For I/O bound RAG workloads, (2 x CPU cores) + 1 is recommended
default_workers = multiprocessing.cpu_count() * 2 + 1
workers = int(os.getenv("WEB_CONCURRENCY", default_workers))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = int(os.getenv("WORKER_CONNECTIONS", "1000"))

# Lifecycle & Timeouts
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))

# Memory leak defense: periodically restart workers after N requests
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "5000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "500"))

# Logging
accesslog = os.getenv("GUNICORN_ACCESSLOG", "-")
errorlog = os.getenv("GUNICORN_ERRORLOG", "-")
loglevel = os.getenv("GUNICORN_LOGLEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(L)ss'

# Process naming
proc_name = "it_support_copilot_api"
