from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.metrics import extract_usage
from app.config import get_settings
from app.modules import billing, prompts, scripts
from app.modules.thumbnails.agents import get_thumbnail_brief_agent, get_thumbnail_brief_agent_entry
from app.modules.thumbnails.events import THUMBNAIL_BRIEF_GENERATED
from app.modules.thumbnails.repository import ThumbnailRepository
from app.shared.events import emit

# DB-editable prompt instructions (prompts module) — fallback used only if
# no active row exists yet for this feature.
_THUMBNAIL_PROMPT_FEATURE = "thumbnail_brief"
_DEFAULT_THUMBNAIL_INSTRUCTIONS = (
    "Produce a text-only thumbnail brief (no image generation) — an "
    "overlay text suggestion, a visual concept description, the "
    "target emotion, and a color direction."
)


class ThumbnailScriptNotFoundError(Exception):
    pass


class ThumbnailGenerationFailedError(Exception):
    """The LLM returned output that couldn't be parsed into a brief —
    transient with free-tier model routing; the caller should retry."""


class ThumbnailService:
    def __init__(self, repository: ThumbnailRepository, db: AsyncSession, redis: Redis):
        self._repository = repository
        self._db = db
        self._redis = redis

    async def generate(self, *, user_id: UUID, script_id: UUID):
        script = await scripts.get_script_for_owner(self._db, script_id, user_id)
        if script is None:
            raise ThumbnailScriptNotFoundError("Script not found")

        instructions, template_version = await prompts.get_active_prompt(
            self._db, self._redis, _THUMBNAIL_PROMPT_FEATURE, _DEFAULT_THUMBNAIL_INSTRUCTIONS
        )
        prompt = f"Script hook: {script.hook}\n{instructions}"
        agent = get_thumbnail_brief_agent()
        result = await agent.arun(prompt)
        brief = result.content
        if isinstance(brief, str) or brief is None:
            raise ThumbnailGenerationFailedError(
                "Thumbnail brief generation returned unparseable output — please retry"
            )
        await prompts.log_invocation(
            self._db,
            feature=_THUMBNAIL_PROMPT_FEATURE,
            template_version=template_version,
            rendered_prompt=prompt,
            reference_id=script_id,
        )
        usage = extract_usage(result)
        agent_entry = get_thumbnail_brief_agent_entry()
        prompt_version = prompts.format_prompt_version(template_version, agent_entry.prompt_version)

        thumbnail_brief = await self._repository.create(
            script_id=script_id,
            brief=brief.model_dump(),
            agent_name="thumbnail_brief",
            agent_version=agent_entry.version,
            prompt_version=prompt_version,
            model_id=get_settings().openrouter_model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost=usage.cost,
        )
        await billing.record_usage(
            self._db,
            user_id,
            datetime.now(UTC).date(),
            feature="thumbnail",
            tokens=usage.input_tokens + usage.output_tokens,
            cost=usage.cost,
        )
        emit(
            THUMBNAIL_BRIEF_GENERATED,
            {"user_id": str(user_id), "thumbnail_brief_id": str(thumbnail_brief.id)},
        )
        return thumbnail_brief
