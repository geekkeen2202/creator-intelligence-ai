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


class UsageSummaryRow(BaseModel):
    """M7 usage dashboard query (TechnicalDesign.md §8 rule 11)."""

    feature: str
    total_tokens: int
    total_cost: float
    total_scripts_generated: int
