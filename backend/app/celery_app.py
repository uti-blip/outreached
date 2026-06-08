"""Celery application + Redis broker config.

Fan-out: 1 job = 1 lead/micro-task, throttled via Redis rate-limiting.
"""

from celery import Celery

from backend.app.config import settings

celery_app = Celery(
    "outreached",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# ── Config ────────────────────────────────────────────
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Paris",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 min hard limit per task
    task_soft_time_limit=240,  # 4 min soft limit
    worker_prefetch_multiplier=1,  # fair dispatch
    worker_max_tasks_per_child=50,  # prevent memory leaks
)

# ── Auto-discover tasks ───────────────────────────────
celery_app.autodiscover_tasks(["backend.app.agents"])
