from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

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
}
