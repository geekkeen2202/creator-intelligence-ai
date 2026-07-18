import json
from datetime import UTC, datetime

import structlog
from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure

from app.config import get_settings

log = structlog.get_logger(__name__)
settings = get_settings()

# How many permanently-failed tasks to keep visible (B10 — a failure past
# max_retries must not just vanish; this is the dead-letter path alongside
# Flower's live dashboard, ARCHITECTURE.md §11).
_DEAD_LETTER_KEY = "dead_letter:tasks"
_DEAD_LETTER_MAX_ENTRIES = 500

celery_app = Celery(
    "creator_intelligence",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.channels_tasks",
        "app.tasks.voice_profile_tasks",
        "app.tasks.channel_niche_tasks",
        "app.tasks.trending_tasks",
        "app.tasks.analytics_tasks",
        "app.tasks.billing_tasks",
        "app.tasks.notifications_tasks",
        "app.tasks.script_outcome_tasks",
    ],
)

celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True

# See ARCHITECTURE.md §11 — jobs are idempotent, safe to retry.
celery_app.conf.beat_schedule = {
    "refresh-trending-topics": {
        "task": "app.tasks.trending_tasks.refresh_trending_topics",
        "schedule": crontab(minute=0, hour=0),  # once daily — see ARCHITECTURE.md §10
    },
    "sync-analytics": {
        "task": "app.tasks.analytics_tasks.sync_analytics",
        "schedule": crontab(minute=0, hour=3),
    },
    "send-weekly-briefing": {
        "task": "app.tasks.notifications_tasks.send_weekly_briefing",
        "schedule": crontab(minute=0, hour=8, day_of_week="monday"),
    },
    "purge-expired-trending-data": {
        "task": "app.tasks.trending_tasks.purge_expired_trending_data",
        "schedule": crontab(minute=0, hour=2, day_of_week="sunday"),
    },
    "refine-voice-profiles": {
        "task": "app.tasks.voice_profile_tasks.refine_voice_profiles",
        "schedule": crontab(minute=0, hour=4, day_of_week="monday"),  # TechnicalDesign.md §5.3 M6
    },
}


@task_failure.connect
def _record_dead_letter(sender=None, task_id=None, exception=None, args=None, kwargs=None, **_):
    """Fires only once a task's retries are exhausted and it truly fails
    (Celery's own retry-via-self.retry() path never reaches this signal) —
    the dead-letter record for a task that would otherwise just vanish.
    """
    task_name = getattr(sender, "name", str(sender))
    log.error(
        "task_dead_letter",
        task_name=task_name,
        task_id=task_id,
        args=repr(args),
        kwargs=repr(kwargs),
        error=str(exception),
    )
    try:
        import redis as redis_sync

        client = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
        entry = json.dumps(
            {
                "task_name": task_name,
                "task_id": task_id,
                "args": repr(args),
                "kwargs": repr(kwargs),
                "error": str(exception),
                "failed_at": datetime.now(UTC).isoformat(),
            }
        )
        client.lpush(_DEAD_LETTER_KEY, entry)
        client.ltrim(_DEAD_LETTER_KEY, 0, _DEAD_LETTER_MAX_ENTRIES - 1)
    except Exception as exc:  # pragma: no cover - best-effort, never mask the real failure
        log.warning("dead_letter_persist_failed", error=str(exc))
