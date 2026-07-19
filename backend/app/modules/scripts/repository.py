from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
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
        topic_id: UUID | None,
        language: str,
        platform: str,
        hook: str,
        body: str,
        cta: str,
        b_roll_suggestions: list[str],
        power_word_spans: list[str],
        duration_estimate_seconds: float | None,
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
            topic_id=topic_id,
            language=language,
            platform=platform,
            hook=hook,
            body=body,
            cta=cta,
            b_roll_suggestions=b_roll_suggestions,
            power_word_spans=power_word_spans,
            duration_estimate_seconds=duration_estimate_seconds,
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

    async def set_rating(
        self, script_id: UUID, rating: int, detail: dict | None = None
    ) -> Script | None:
        script = await self.get_by_id(script_id)
        if script is not None:
            script.rating = rating
            script.rating_detail = detail
            await self._db.commit()
        return script

    async def set_final_text(self, script_id: UUID, final_text: str) -> Script | None:
        script = await self.get_by_id(script_id)
        if script is not None:
            script.final_text = final_text
            await self._db.commit()
        return script

    async def list_feedback_since(self, channel_id: UUID, since: datetime) -> list[Script]:
        """Scripts with a rating or a creator edit since `since` — the raw
        material for weekly refinement (TechnicalDesign.md §5.3)."""
        result = await self._db.execute(
            select(Script).where(
                Script.channel_id == channel_id,
                Script.updated_at >= since,
                (Script.rating.is_not(None)) | (Script.final_text.is_not(None)),
            )
        )
        return list(result.scalars().all())

    async def list_unmatched_for_channel(self, channel_id: UUID) -> list[Script]:
        """Scripts on this channel with no script_outcomes row yet — the
        candidate pool for auto outcome-matching (TechnicalDesign.md §5.5)."""
        result = await self._db.execute(
            select(Script)
            .outerjoin(ScriptOutcome, ScriptOutcome.script_id == Script.id)
            .where(Script.channel_id == channel_id, ScriptOutcome.id.is_(None))
        )
        return list(result.scalars().all())

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
        # ON CONFLICT DO NOTHING: a retried link_script_outcome/
        # auto_link_script_outcomes task must not duplicate the row
        # (ARCHITECTURE.md §11 idempotency) — one outcome per script.
        stmt = (
            insert(ScriptOutcome)
            .values(
                script_id=script_id,
                external_video_id=external_video_id,
                matched_by=matched_by,
                ctr=ctr,
                avg_view_duration=avg_view_duration,
                views=views,
            )
            .on_conflict_do_nothing(index_elements=[ScriptOutcome.script_id])
            .returning(ScriptOutcome)
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        outcome = result.scalar_one_or_none()
        if outcome is not None:
            return outcome
        existing = await self._db.execute(
            select(ScriptOutcome).where(ScriptOutcome.script_id == script_id)
        )
        return existing.scalar_one()

    async def get_outcome_for_script(self, script_id: UUID) -> ScriptOutcome | None:
        result = await self._db.execute(
            select(ScriptOutcome).where(ScriptOutcome.script_id == script_id)
        )
        return result.scalars().first()

    async def has_outcome_for_video(self, external_video_id: str) -> bool:
        result = await self._db.execute(
            select(ScriptOutcome.id).where(ScriptOutcome.external_video_id == external_video_id)
        )
        return result.scalars().first() is not None

    async def list_outcome_signals_since(
        self, channel_id: UUID, since: datetime
    ) -> list[tuple[str, int]]:
        """(hook, views) pairs for scripts on this channel matched to an
        outcome since `since`, best-performing first — the performance-based
        refinement signal (TechnicalDesign.md §5.3), distinct from creator
        ratings/edits."""
        result = await self._db.execute(
            select(Script.hook, ScriptOutcome.views)
            .join(ScriptOutcome, ScriptOutcome.script_id == Script.id)
            .where(
                Script.channel_id == channel_id,
                Script.created_at >= since,
                ScriptOutcome.views.is_not(None),
            )
            .order_by(ScriptOutcome.views.desc())
        )
        return [(row.hook, row.views) for row in result]

    async def rating_summary_by_profile_version(self, channel_id: UUID) -> list[dict]:
        """M6 exit criterion (TechnicalDesign.md §6.3): "the version-comparison
        query answers v2 vs v1 without manual archaeology." Ratings grouped
        by the Voice DNA version that generated each script.
        """
        result = await self._db.execute(
            select(
                Script.voice_profile_version,
                func.count(Script.id).label("rated_count"),
                func.avg(Script.rating).label("avg_rating"),
            )
            .where(Script.channel_id == channel_id, Script.rating.is_not(None))
            .group_by(Script.voice_profile_version)
            .order_by(Script.voice_profile_version)
        )
        return [
            {
                "voice_profile_version": row.voice_profile_version,
                "rated_count": row.rated_count,
                "avg_rating": float(row.avg_rating) if row.avg_rating is not None else None,
            }
            for row in result
        ]
