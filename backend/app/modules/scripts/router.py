from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import channels
from app.modules.scripts.repository import ScriptRepository
from app.modules.scripts.schemas import (
    ScriptFinalTextRequest,
    ScriptGenerateRequest,
    ScriptPublishRequest,
    ScriptRateRequest,
    ScriptRead,
    VoiceProfileRatingSummary,
)
from app.modules.scripts.service import (
    ScriptGenerationFailedError,
    ScriptGenerationLimitError,
    ScriptNotFoundError,
    ScriptService,
)
from app.shared.cache import get_redis
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/scripts", tags=["scripts"])


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> ScriptService:
    return ScriptService(ScriptRepository(db), db, redis)


@router.post("", response_model=ScriptRead, status_code=status.HTTP_201_CREATED)
async def generate_script(
    body: ScriptGenerateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ScriptService, Depends(get_service)],
):
    try:
        return await service.generate(
            user_id=UUID(user.user_id),
            channel_id=body.channel_id,
            topic=body.topic,
            topic_id=body.topic_id,
            language=body.language,
            platform=body.platform,
            premium=body.premium,
        )
    except ScriptGenerationLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except ScriptGenerationFailedError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/{script_id}/stream")
async def stream_script(
    script_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ScriptService, Depends(get_service)],
):
    try:
        agent, prompt = await service.prepare_stream(
            user_id=UUID(user.user_id), script_id=script_id
        )
    except ScriptGenerationLimitError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    except ScriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    async def event_source():
        # arun(stream=True) returns the async generator directly (not an
        # awaitable). Not every streamed event carries text content — skip
        # tool/metadata events instead of yielding "None".
        async for chunk in agent.arun(prompt, stream=True):
            content = getattr(chunk, "content", None)
            if isinstance(content, str) and content:
                yield f"data: {content}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post("/{script_id}/rate", response_model=ScriptRead)
async def rate_script(
    script_id: UUID,
    body: ScriptRateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ScriptService, Depends(get_service)],
):
    try:
        script = await service.rate(
            user_id=UUID(user.user_id), script_id=script_id, rating=body.rating, detail=body.detail
        )
    except ScriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return script


@router.patch("/{script_id}/final-text", response_model=ScriptRead)
async def set_final_text(
    script_id: UUID,
    body: ScriptFinalTextRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ScriptService, Depends(get_service)],
):
    try:
        script = await service.set_final_text(
            user_id=UUID(user.user_id), script_id=script_id, final_text=body.final_text
        )
    except ScriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return script


@router.get("/measurement/{channel_id}", response_model=list[VoiceProfileRatingSummary])
async def get_rating_measurement(
    channel_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    service: Annotated[ScriptService, Depends(get_service)],
):
    """M6 measurement query (TechnicalDesign.md §6.3) — "does profile v(n+1)
    beat v(n)?" answered by query, not manual archaeology."""
    if not await channels.verify_ownership(db, channel_id, UUID(user.user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return await service.get_rating_summary_by_profile_version(channel_id)


@router.post("/{script_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def publish_script(
    script_id: UUID,
    body: ScriptPublishRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ScriptService, Depends(get_service)],
):
    try:
        await service.publish(
            user_id=UUID(user.user_id),
            script_id=script_id,
            external_video_id=body.external_video_id,
        )
    except ScriptNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
