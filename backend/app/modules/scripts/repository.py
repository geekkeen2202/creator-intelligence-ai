from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.scripts.models import Script, ScriptOutcome


class ScriptRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_id(self, script_id: UUID) -> Script | None:
        return await self._db.get(Script, script_id)

    async def create(
        self,
        *,
        user_id: UUID,
        channel_id: UUID,
        topic: str,
        hook: str,
        body: str,
        cta: str,
        voice_profile_version: int | None,
        agent_name: str,
        agent_version: str,
        prompt_version: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> Script:
        script = Script(
            user_id=user_id,
            channel_id=channel_id,
            topic=topic,
            hook=hook,
            body=body,
            cta=cta,
            voice_profile_version=voice_profile_version,
            agent_name=agent_name,
            agent_version=agent_version,
            prompt_version=prompt_version,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        self._db.add(script)
        await self._db.commit()
        await self._db.refresh(script)
        return script

    async def set_rating(self, script_id: UUID, rating: int) -> Script | None:
        script = await self.get_by_id(script_id)
        if script is not None:
            script.rating = rating
            await self._db.commit()
        return script

    async def create_outcome(
        self,
        *,
        script_id: UUID,
        external_video_id: str,
        matched_by: str,
        ctr: float | None,
        avg_view_duration: float | None,
        views: int | None,
    ) -> ScriptOutcome:
        outcome = ScriptOutcome(
            script_id=script_id,
            external_video_id=external_video_id,
            matched_by=matched_by,
            ctr=ctr,
            avg_view_duration=avg_view_duration,
            views=views,
        )
        self._db.add(outcome)
        await self._db.commit()
        await self._db.refresh(outcome)
        return outcome

    async def get_outcome_for_script(self, script_id: UUID) -> ScriptOutcome | None:
        result = await self._db.execute(
            select(ScriptOutcome).where(ScriptOutcome.script_id == script_id)
        )
        return result.scalars().first()
