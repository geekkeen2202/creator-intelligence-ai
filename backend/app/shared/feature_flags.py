"""Feature flags: DB table + Redis cache (ARCHITECTURE.md §9, §10; ~5min TTL).
Infrastructure, not a feature module — every module can gate behind a flag
without owning flag storage itself.
"""

import hashlib
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import Boolean, Float, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database import Base
from app.shared.models import BaseModelMixin

_CACHE_TTL_SECONDS = 5 * 60


class FeatureFlag(BaseModelMixin, Base):
    __tablename__ = "feature_flags"

    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Gradual rollout: 0-100. A user is in the rollout if a stable hash of
    # (flag name, user_id) falls under this percentage — same user always
    # gets the same answer for a given percentage, no flapping.
    rollout_percentage: Mapped[float] = mapped_column(Float, default=100.0)
    description: Mapped[str] = mapped_column(String(256), default="")


def _in_rollout(flag_name: str, user_id: UUID, percentage: float) -> bool:
    if percentage >= 100:
        return True
    if percentage <= 0:
        return False
    digest = hashlib.sha256(f"{flag_name}:{user_id}".encode()).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < percentage


async def is_enabled(
    db: AsyncSession, redis: Redis, flag_name: str, user_id: UUID, *, default: bool = False
) -> bool:
    cache_key = f"flags:{flag_name}"
    cached = await redis.get(cache_key)
    if cached is not None:
        enabled, percentage = cached.split(":", 1)
        if enabled == "0":
            return False
        return _in_rollout(flag_name, user_id, float(percentage))

    result = await db.execute(select(FeatureFlag).where(FeatureFlag.name == flag_name))
    flag = result.scalars().first()
    if flag is None:
        return default

    await redis.set(
        cache_key, f"{int(flag.enabled)}:{flag.rollout_percentage}", ex=_CACHE_TTL_SECONDS
    )
    if not flag.enabled:
        return False
    return _in_rollout(flag_name, user_id, flag.rollout_percentage)


async def ensure_flag_exists(
    db: AsyncSession, name: str, *, enabled: bool = False, description: str = ""
) -> None:
    """Idempotent registration — called at startup so every known flag has a
    row even before anyone flips it (ARCHITECTURE.md §7: script_team ships
    registered and disabled)."""
    result = await db.execute(select(FeatureFlag).where(FeatureFlag.name == name))
    if result.scalars().first() is not None:
        return
    db.add(FeatureFlag(name=name, enabled=enabled, description=description))
    await db.commit()
