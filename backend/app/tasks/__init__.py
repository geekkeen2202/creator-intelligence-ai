"""Importing this package registers every @subscribe(...) handler with the
event bus (app/shared/events.py). The web process must import it too, not
just Celery workers — emit() only dispatches to handlers registered in the
CURRENT process, and FastAPI's own process is where `emit()` gets called
from request/service code (e.g. CHANNEL_CONNECTED, TRENDING_COLD_NICHE_REQUESTED).
Without this import, those emits are silent no-ops in the API process even
with a Celery worker running elsewhere.
"""

from app.tasks import (
    analytics_tasks,
    billing_tasks,
    channel_niche_tasks,
    channels_tasks,
    notifications_tasks,
    script_outcome_tasks,
    trending_tasks,
    voice_profile_tasks,
)

__all__ = [
    "analytics_tasks",
    "billing_tasks",
    "channel_niche_tasks",
    "channels_tasks",
    "notifications_tasks",
    "script_outcome_tasks",
    "trending_tasks",
    "voice_profile_tasks",
]
