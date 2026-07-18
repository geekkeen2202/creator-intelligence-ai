from redis.asyncio import Redis


class RedisRateLimiter:
    """Fixed-window rate limiter shared across callers (see ARCHITECTURE.md §10 key pattern).

    Generalizes the INCR+EXPIRE check first written ad-hoc in ScriptService —
    same semantics, reusable for any key/limit/window (e.g. per-external-source
    ingestion budgets, not just per-user request limits).
    """

    def __init__(self, redis: Redis, key: str, limit: int, window_seconds: int):
        self._redis = redis
        self._key = key
        self._limit = limit
        self._window_seconds = window_seconds

    async def allow(self) -> bool:
        count = await self._redis.incr(self._key)
        if count == 1:
            await self._redis.expire(self._key, self._window_seconds)
        return count <= self._limit
