from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.voice_profiles import service
from app.modules.voice_profiles.schemas import VoiceProfileRead
from app.shared.database import get_db

router = APIRouter(prefix="/voice-profiles", tags=["voice_profiles"])


@router.get("/{channel_id}", response_model=VoiceProfileRead)
async def get_channel_voice_profile(
    channel_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    voice_profile = await service.get_current(db, channel_id)
    if voice_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No Voice DNA profile yet"
        )
    return voice_profile
