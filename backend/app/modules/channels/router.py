from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import voice_profiles
from app.modules.channels.repository import ChannelRepository
from app.modules.channels.schemas import ChannelConnectRequest, ChannelRead, ChannelStatusRead
from app.modules.channels.service import ChannelService
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/channels", tags=["channels"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> ChannelService:
    return ChannelService(ChannelRepository(db))


@router.post("", response_model=ChannelRead, status_code=status.HTTP_201_CREATED)
async def connect_channel(
    body: ChannelConnectRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ChannelService, Depends(get_service)],
):
    return await service.connect_channel(
        user_id=UUID(user.user_id),
        platform=body.platform,
        external_channel_id=body.external_channel_id,
        handle=body.handle,
    )


@router.get("", response_model=list[ChannelRead])
async def list_channels(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ChannelService, Depends(get_service)],
):
    return await service.list_channels(UUID(user.user_id))


@router.get("/{channel_id}", response_model=ChannelRead)
async def get_channel(
    channel_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[ChannelService, Depends(get_service)],
):
    channel = await service.get_channel(channel_id)
    # 404 (not 403) either way — a 403 would confirm the id exists but
    # belongs to someone else, leaking its existence to a guessing attacker.
    if channel is None or channel.user_id != UUID(user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return channel


@router.get("/{channel_id}/status", response_model=ChannelStatusRead)
async def get_channel_status(
    channel_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Onboarding progressive status (TechnicalDesign.md §5.1) — how far the
    ingest_channel -> transcribe_video -> extract_voice_profile chain has
    gotten, so the frontend can render "32 of 50 analysed" instead of a
    black-box spinner.
    """
    channel = await ChannelRepository(db).get_by_id(channel_id)
    if channel is None or channel.user_id != UUID(user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return await voice_profiles.get_ingestion_status(db, channel_id)
