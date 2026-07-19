from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.script_agent import SHORT_FORM_PLATFORMS
from app.ai.metrics import extract_usage
from app.config import get_settings
from app.modules import billing, prompts, trending, voice_profiles
from app.modules.scripts.agents import (
    get_script_agent,
    get_script_agent_entry,
    get_script_stream_agent,
    get_script_team,
)
from app.modules.scripts.events import SCRIPT_GENERATED, SCRIPT_PUBLISHED, SCRIPT_RATED
from app.modules.scripts.repository import ScriptRepository
from app.shared import feature_flags
from app.shared.events import emit

# Ships registered and disabled (ARCHITECTURE.md §7) — premium requests use
# the single agent until a measured quality win enables the team pipeline.
_SCRIPT_TEAM_FLAG = "script_team"

# Per-tier hourly generation limits (TechnicalDesign.md §5.2/M4.3 — "premium
# tiers differentiate on limits and features, not pipeline", §7). Numbers
# are placeholders pending real pricing decisions; unknown/no plan = free.
_RATE_LIMIT_BY_PLAN = {
    "free": 5,
    "creator": 20,
    "unlimited": 200,
}
_DEFAULT_RATE_LIMIT_PER_HOUR = _RATE_LIMIT_BY_PLAN["free"]

# DB-editable prompt instructions (prompts module) — fallback used only if
# no active row exists yet for this feature.
_SCRIPT_PROMPT_FEATURE = "script_generation"
_DEFAULT_SCRIPT_INSTRUCTIONS = "Write a short-form video script using the following context."


class ScriptGenerationLimitError(Exception):
    pass


class ScriptNotFoundError(Exception):
    pass


class ScriptGenerationFailedError(Exception):
    """The LLM returned output that couldn't be parsed into a script —
    transient with free-tier model routing; the caller should retry."""


def _model_id_for(platform: str) -> str:
    settings = get_settings()
    if platform in SHORT_FORM_PLATFORMS:
        return settings.openrouter_fast_model or settings.openrouter_model
    return settings.openrouter_model


class ScriptService:
    def __init__(self, repository: ScriptRepository, db: AsyncSession, redis: Redis):
        self._repository = repository
        self._db = db
        self._redis = redis

    async def _check_rate_limit(self, user_id: UUID) -> None:
        plan = await billing.get_active_plan(self._db, user_id)
        limit = _RATE_LIMIT_BY_PLAN.get(plan, _DEFAULT_RATE_LIMIT_PER_HOUR)

        key = f"ratelimit:{user_id}:script_generate"
        count = await self._redis.incr(key)
        if count == 1:
            await self._redis.expire(key, 60 * 60)
        if count > limit:
            raise ScriptGenerationLimitError("Hourly script generation limit reached")

    async def _build_prompt(self, channel_id: UUID, topic: str) -> tuple[str, int | None]:
        instructions, template_version = await prompts.get_active_prompt(
            self._db, self._redis, _SCRIPT_PROMPT_FEATURE, _DEFAULT_SCRIPT_INSTRUCTIONS
        )
        voice_dna_block = await voice_profiles.get_prompt_block(self._db, self._redis, channel_id)
        context = await trending.get_channel_context(self._db, self._redis, channel_id)
        prompt = (
            f"{instructions}\n"
            f"Topic: {topic}\n"
            f"{voice_dna_block}\n"
            f"Trending topics in this creator's niche: "
            f"{', '.join(t.title for t in context.topics[:5]) or 'none yet'}\n"
            f"Top competitor videos: "
            f"{'; '.join(v.summary or v.title for v in context.videos[:3]) or 'none yet'}"
        )
        return prompt, template_version

    async def generate(
        self,
        *,
        user_id: UUID,
        channel_id: UUID,
        topic: str,
        topic_id: UUID | None = None,
        language: str = "en",
        platform: str = "youtube_long",
        premium: bool,
    ):
        await self._check_rate_limit(user_id)

        # script_team is feature-flagged off by default (ARCHITECTURE.md §7)
        # — until it measurably beats the single agent, premium requests
        # silently fall back to it rather than erroring.
        use_team = premium and await feature_flags.is_enabled(
            self._db, self._redis, _SCRIPT_TEAM_FLAG, user_id, default=False
        )

        prompt, template_version = await self._build_prompt(channel_id, topic)
        agent = get_script_team() if use_team else get_script_agent(platform)
        result = await agent.arun(prompt)
        generated = result.content
        if isinstance(generated, str) or generated is None:
            raise ScriptGenerationFailedError(
                "Script generation returned unparseable output — please retry"
            )
        await prompts.log_invocation(
            self._db,
            feature=_SCRIPT_PROMPT_FEATURE,
            template_version=template_version,
            rendered_prompt=prompt,
            reference_id=channel_id,
        )
        usage = extract_usage(result)
        agent_entry = get_script_agent_entry(use_team)
        voice_profile_version = await voice_profiles.get_current_profile_version(
            self._db, channel_id
        )
        prompt_version = prompts.format_prompt_version(template_version, agent_entry.prompt_version)

        script = await self._repository.create(
            user_id=user_id,
            channel_id=channel_id,
            topic=topic,
            topic_id=topic_id,
            language=language,
            platform=platform,
            hook=generated.hook,
            body=generated.body,
            cta=generated.cta,
            b_roll_suggestions=generated.b_roll_suggestions,
            power_word_spans=generated.power_word_spans,
            duration_estimate_seconds=generated.estimated_duration_seconds,
            voice_profile_version=voice_profile_version,
            agent_name="script_team" if use_team else "script",
            agent_version=agent_entry.version,
            prompt_version=prompt_version,
            model_id=get_settings().openrouter_model if use_team else _model_id_for(platform),
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

        prompt, template_version = await self._build_prompt(script.channel_id, script.topic)
        await prompts.log_invocation(
            self._db,
            feature=_SCRIPT_PROMPT_FEATURE,
            template_version=template_version,
            rendered_prompt=prompt,
            reference_id=script.channel_id,
        )
        return get_script_stream_agent(script.platform), prompt

    async def rate(
        self, *, user_id: UUID, script_id: UUID, rating: int, detail: dict | None = None
    ):
        # Ownership check is mandatory (ARCHITECTURE.md §4 rule 7) — ratings
        # feed the refinement loop, so a foreign rating would poison another
        # creator's Voice DNA.
        script = await self._repository.get_by_id(script_id)
        if script is None or script.user_id != user_id:
            raise ScriptNotFoundError("Script not found")
        script = await self._repository.set_rating(script_id, rating, detail)
        emit(
            SCRIPT_RATED,
            {"script_id": str(script_id), "rating": rating, "detail": detail},
        )
        return script

    async def set_final_text(self, *, user_id: UUID, script_id: UUID, final_text: str):
        """Persists the creator-edited version — the richest refinement
        signal (TechnicalDesign.md §5.3): diffing this vs the generated
        hook/body/cta shows what the creator actually changed.
        """
        script = await self._repository.get_by_id(script_id)
        if script is None or script.user_id != user_id:
            raise ScriptNotFoundError("Script not found")
        return await self._repository.set_final_text(script_id, final_text)

    async def get_rating_summary_by_profile_version(self, channel_id: UUID):
        """M6 measurement query (TechnicalDesign.md §6.3) — ownership is
        checked by the router (channel-scoped read, same pattern as
        analytics/voice_profiles)."""
        return await self._repository.rating_summary_by_profile_version(channel_id)

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
