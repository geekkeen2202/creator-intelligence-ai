from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.prompts.models import PromptInvocation, PromptTemplate


class PromptRepository:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_active_template(self, feature: str) -> PromptTemplate | None:
        result = await self._db.execute(
            select(PromptTemplate).where(
                PromptTemplate.feature == feature, PromptTemplate.is_active.is_(True)
            )
        )
        return result.scalars().first()

    async def list_versions(self, feature: str) -> list[PromptTemplate]:
        result = await self._db.execute(
            select(PromptTemplate)
            .where(PromptTemplate.feature == feature)
            .order_by(PromptTemplate.version.desc())
        )
        return list(result.scalars().all())

    async def _get_latest_version_number(self, feature: str) -> int:
        result = await self._db.execute(
            select(func.max(PromptTemplate.version)).where(PromptTemplate.feature == feature)
        )
        return result.scalar_one() or 0

    async def create_version(self, *, feature: str, template: str) -> PromptTemplate:
        # Deactivate the current active row first — the partial unique index
        # (uq_prompt_templates_feature_active) would reject a second active
        # row for the same feature otherwise.
        await self._db.execute(
            update(PromptTemplate)
            .where(PromptTemplate.feature == feature, PromptTemplate.is_active.is_(True))
            .values(is_active=False)
        )
        next_version = await self._get_latest_version_number(feature) + 1
        row = PromptTemplate(
            feature=feature, version=next_version, template=template, is_active=True
        )
        self._db.add(row)
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def log_invocation(
        self,
        *,
        feature: str,
        template_version: int | None,
        rendered_prompt: str,
        reference_id: UUID | None,
    ) -> None:
        self._db.add(
            PromptInvocation(
                feature=feature,
                template_version=template_version,
                rendered_prompt=rendered_prompt,
                reference_id=reference_id,
            )
        )
        await self._db.commit()
