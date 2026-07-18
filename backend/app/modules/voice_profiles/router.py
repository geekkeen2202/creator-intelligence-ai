from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules import channels
from app.modules.voice_profiles import service
from app.modules.voice_profiles.schemas import VoiceProfileRead
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

router = APIRouter(prefix="/voice-profiles", tags=["voice_profiles"])


@router.get("/{channel_id}", response_model=VoiceProfileRead)
async def get_channel_voice_profile(
    channel_id: UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not await channels.verify_ownership(db, channel_id, UUID(user.user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    voice_profile = await service.get_current(db, channel_id)
    if voice_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No Voice DNA profile yet"
        )
    return voice_profile
