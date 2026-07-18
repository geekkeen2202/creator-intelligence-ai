"""In-process event bus. Emitters never know who listens.

Subscribers are Celery tasks (or plain callables in tests) registered via
`@subscribe("event.name")`. `emit` dispatches to Celery when the handler is a
task (has `.delay`), otherwise calls it synchronously — useful in tests.
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import structlog

log = structlog.get_logger(__name__)

EventPayload = dict[str, Any]

_subscribers: dict[str, list[Callable[[EventPayload], Any]]] = defaultdict(list)

# Fire-and-forget persistence tasks scheduled by emit() (see
# _persist_best_effort). asyncio.run() only waits for the coroutine passed
# to it — a task merely scheduled via create_task() and never awaited gets
# silently cancelled the moment that coroutine returns and the loop closes.
# Every Celery task wraps its body in a fresh asyncio.run() per call, so
# without draining this set before that return, every event emitted from a
# task body (as opposed to a FastAPI request, whose loop stays alive across
# requests) never actually reaches the events table. run_with_event_flush()
# below is how task entrypoints avoid that.
_pending_persist_tasks: set[asyncio.Task] = set()

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
        task = asyncio.get_running_loop().create_task(_persist(event_name, payload))
        _pending_persist_tasks.add(task)
        task.add_done_callback(_pending_persist_tasks.discard)
    except RuntimeError:
        log.warning("event_log_skipped_no_event_loop", event_name=event_name)


async def drain_pending_event_log_writes() -> None:
    """Await every event-log write scheduled so far. Must run before a
    short-lived event loop (a Celery task's asyncio.run()) closes — see
    run_with_event_flush(), the entrypoint helper that does this for you.
    """
    pending = [t for t in _pending_persist_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def run_with_event_flush(coro):
    """Drop-in replacement for `asyncio.run(coro)` in every Celery task
    entrypoint — runs the task body, then drains any events it emitted
    before the loop closes, so audit-log writes from task contexts aren't
    silently dropped (see _pending_persist_tasks above).
    """

    async def _wrapped():
        result = await coro
        await drain_pending_event_log_writes()
        return result

    return asyncio.run(_wrapped())


@lru_cache
def _get_event_log_engine():
    # One AsyncEngine object for the process lifetime, not one per emitted
    # event (that was a connection-storm-under-load bug — creating an engine
    # per event is expensive even though NullPool means no idle connections
    # linger). Safe to share across event loops: NullPool never reuses a
    # connection between calls, so each `.connect()` opens a fresh asyncpg
    # connection bound to whichever loop is running at that moment — exactly
    # what lets this same engine serve both FastAPI's one stable loop and
    # every distinct asyncio.run() loop a Celery task creates.
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import get_settings

    return create_async_engine(get_settings().database_url, poolclass=NullPool)


async def _persist(event_name: str, payload: EventPayload) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.shared.event_log import EventLog

    engine = _get_event_log_engine()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(EventLog(event_name=event_name, payload=payload))
            await session.commit()
    except Exception as exc:  # pragma: no cover - best-effort audit log only
        log.warning("event_log_persist_failed", event_name=event_name, error=str(exc))
