import asyncio

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.resend_adapter import ResendAdapter
from app.config import get_settings
from app.modules import users
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.tasks.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(
    name="app.tasks.notifications_tasks.send_weekly_briefing", bind=True, max_retries=3
)
def send_weekly_briefing(self) -> None:
    """Runs Monday mornings via Celery Beat — sends a weekly briefing to every user."""
    try:
        asyncio.run(_send_all())
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _send_all() -> None:
    settings = get_settings()
    if not settings.resend_api_key:
        log.info("weekly_briefing_skipped_no_resend_key")
        return

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user_rows = await users.list_all_user_emails(session)
            service = NotificationService(NotificationRepository(session), ResendAdapter())

            for user_id, email in user_rows:
                try:
                    # Per-channel/per-niche personalization (top_topics) is a
                    # follow-up — this sends a generic briefing to every user
                    # for now, which is enough to make the job actually work.
                    await service.send_weekly_briefing(user_id, email, {"top_topics": []})
                except Exception as exc:
                    log.warning("weekly_briefing_send_failed", user_id=str(user_id), error=str(exc))
    finally:
        await engine.dispose()
