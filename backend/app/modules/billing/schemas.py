from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SubscriptionCreateRequest(BaseModel):
    plan: str


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    plan: str
    status: str
