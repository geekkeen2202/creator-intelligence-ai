"""In-process event bus. Emitters never know who listens.

Subscribers are Celery tasks (or plain callables in tests) registered via
`@subscribe("event.name")`. `emit` dispatches to Celery when the handler is a
task (has `.delay`), otherwise calls it synchronously — useful in tests.
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)

EventPayload = dict[str, Any]

_subscribers: dict[str, list[Callable[[EventPayload], Any]]] = defaultdict(list)

# Canonical events (ARCHITECTURE.md §5) — versioned, additive-only payloads.
USER_REGISTERED = "user.registered"
CHANNEL_CONNECTED = "channel.connected"
CHANNEL_ANALYZED = "channel.analyzed"
TRANSCRIPTS_COMPLETED = "transcripts.completed"
VOICE_PROFILE_UPDATED = "voice_profile.updated"
SCRIPT_GENERATED = "script.generated"
SCRIPT_RATED = "script.rated"
SCRIPT_PUBLISHED = "script.published"
SCRIPT_OUTCOME_LINKED = "script.outcome_linked"
TOPIC_BOOKMARKED = "topic.bookmarked"
SUBSCRIPTION_STARTED = "subscription.started"
SUBSCRIPTION_CANCELLED = "subscription.cancelled"
SUBSCRIPTION_PAYMENT_FAILED = "subscription.payment_failed"
ANALYTICS_SYNCED = "analytics.synced"
TRENDING_REFRESHED = "trending.refreshed"
TRENDING_COLD_NICHE_REQUESTED = "trending.cold_niche_requested"


def subscribe(event_name: str) -> Callable[[Callable], Callable]:
    def decorator(func: Callable) -> Callable:
        _subscribers[event_name].append(func)
        return func

    return decorator


def emit(event_name: str, payload: EventPayload) -> None:
    _persist_best_effort(event_name, payload)
    for handler in _subscribers[event_name]:
        if hasattr(handler, "delay"):
            handler.delay(payload)
        else:
            handler(payload)


def _persist_best_effort(event_name: str, payload: EventPayload) -> None:
    """Append to the events audit log (§5) without making emit() async or
    threading a DB session through every call site. Best-effort: a logging
    failure here must never break the actual event dispatch above.
    """
    try:
        asyncio.get_running_loop().create_task(_persist(event_name, payload))
    except RuntimeError:
        log.warning("event_log_skipped_no_event_loop", event_name=event_name)


async def _persist(event_name: str, payload: EventPayload) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import get_settings
    from app.shared.event_log import EventLog

    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(EventLog(event_name=event_name, payload=payload))
            await session.commit()
    except Exception as exc:  # pragma: no cover - best-effort audit log only
        log.warning("event_log_persist_failed", event_name=event_name, error=str(exc))
    finally:
        await engine.dispose()
