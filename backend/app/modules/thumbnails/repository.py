from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.thumbnails.models import ThumbnailBrief


class ThumbnailRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_by_id(self, thumbnail_brief_id: UUID) -> ThumbnailBrief | None:
        return await self._db.get(ThumbnailBrief, thumbnail_brief_id)

    async def create(
        self,
        *,
        script_id: UUID,
        brief: dict,
        agent_name: str,
        agent_version: str,
        prompt_version: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> ThumbnailBrief:
        thumbnail_brief = ThumbnailBrief(
            script_id=script_id,
            brief=brief,
            agent_name=agent_name,
            agent_version=agent_version,
            prompt_version=prompt_version,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        )
        self._db.add(thumbnail_brief)
        await self._db.commit()
        await self._db.refresh(thumbnail_brief)
        return thumbnail_brief
