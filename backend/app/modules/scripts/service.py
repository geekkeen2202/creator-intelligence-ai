from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.metrics import extract_usage
from app.config import get_settings
from app.modules import billing, trending, voice_profiles
from app.modules.scripts.agents import (
    get_script_agent,
    get_script_agent_entry,
    get_script_stream_agent,
    get_script_team,
)
from app.modules.scripts.events import SCRIPT_GENERATED, SCRIPT_PUBLISHED, SCRIPT_RATED
from app.modules.scripts.repository import ScriptRepository
from app.shared.events import emit

_RATE_LIMIT_PER_HOUR = 20


class ScriptGenerationLimitError(Exception):
    pass


class ScriptNotFoundError(Exception):
    pass


class ScriptGenerationFailedError(Exception):
    """The LLM returned output that couldn't be parsed into a script —
    transient with free-tier model routing; the caller should retry."""


class ScriptService:
    def __init__(self, repository: ScriptRepository, db: AsyncSession, redis: Redis):
        self._repository = repository
        self._db = db
        self._redis = redis

    async def _check_rate_limit(self, user_id: UUID) -> None:
        key = f"ratelimit:{user_id}:script_generate"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 60 * 60)
        if count > _RATE_LIMIT_PER_HOUR:
            raise ScriptGenerationLimitError("Hourly script generation limit reached")

    async def _build_prompt(self, channel_id: UUID, topic: str) -> str:
        voice_dna_block = await voice_profiles.get_prompt_block(self._db, self._redis, channel_id)
        context = await trending.get_channel_context(self._db, self._redis, channel_id)
        return (
            f"Topic: {topic}\n"
            f"{voice_dna_block}\n"
            f"Trending topics in this creator's niche: "
            f"{', '.join(t.title for t in context.topics[:5]) or 'none yet'}\n"
            f"Top competitor videos: "
            f"{'; '.join(v.summary or v.title for v in context.videos[:3]) or 'none yet'}"
        )

    async def generate(self, *, user_id: UUID, channel_id: UUID, topic: str, premium: bool):
        await self._check_rate_limit(user_id)

        prompt = await self._build_prompt(channel_id, topic)
        agent = get_script_team() if premium else get_script_agent()
        result = await agent.arun(prompt)
        generated = result.content
        if isinstance(generated, str) or generated is None:
            raise ScriptGenerationFailedError(
                "Script generation returned unparseable output — please retry"
            )
        usage = extract_usage(result)
        agent_entry = get_script_agent_entry(premium)
        voice_profile_version = await voice_profiles.get_current_profile_version(
            self._db, channel_id
        )

        script = await self._repository.create(
            user_id=user_id,
            channel_id=channel_id,
            topic=topic,
            hook=generated.hook,
            body=generated.body,
            cta=generated.cta,
            voice_profile_version=voice_profile_version,
            agent_name="script_team" if premium else "script",
            agent_version=agent_entry.version,
            prompt_version=agent_entry.prompt_version,
            model_id=get_settings().openrouter_model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost=usage.cost,
        )
        await billing.increment_script_usage(self._db, user_id, datetime.now(UTC).date())
        await billing.record_usage(
            self._db,
            user_id,
            datetime.now(UTC).date(),
            feature="script",
            tokens=usage.input_tokens + usage.output_tokens,
            cost=usage.cost,
        )
        emit(SCRIPT_GENERATED, {"user_id": str(user_id), "script_id": str(script.id)})
        return script

    async def prepare_stream(self, *, user_id: UUID, script_id: UUID):
        """Validates auth/ownership/rate-limit and returns a ready-to-run agent
        + prompt for the router to stream — kept separate from the actual SSE
        generator so 404/429 raise before any StreamingResponse starts.
        """
        script = await self._repository.get_by_id(script_id)
        if script is None or script.user_id != user_id:
            raise ScriptNotFoundError("Script not found")

        await self._check_rate_limit(user_id)

        prompt = await self._build_prompt(script.channel_id, script.topic)
        return get_script_stream_agent(), prompt

    async def rate(self, script_id: UUID, rating: int):
        script = await self._repository.set_rating(script_id, rating)
        if script is not None:
            emit(SCRIPT_RATED, {"script_id": str(script_id), "rating": rating})
        return script

    async def publish(self, *, user_id: UUID, script_id: UUID, external_video_id: str) -> None:
        """Marks a script as published to a specific video — triggers the
        post-publish loop (ARCHITECTURE.md §1/§11 link_script_outcome job).
        """
        script = await self._repository.get_by_id(script_id)
        if script is None or script.user_id != user_id:
            raise ScriptNotFoundError("Script not found")
        emit(
            SCRIPT_PUBLISHED,
            {"script_id": str(script_id), "external_video_id": external_video_id},
        )
