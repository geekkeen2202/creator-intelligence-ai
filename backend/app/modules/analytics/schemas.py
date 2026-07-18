from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AnalyticsDailyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    channel_id: UUID
    day: date
    views: int
    watch_time_minutes: int
    subscribers_gained: int
