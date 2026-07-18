from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    template_name: str
    status: str
