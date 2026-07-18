from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.razorpay_adapter import RazorpayAdapter
from app.modules.billing.repository import BillingRepository
from app.modules.billing.schemas import SubscriptionCreateRequest, SubscriptionRead, UsageSummaryRow
from app.modules.billing.service import BillingService
from app.shared.database import get_db
from app.shared.security import CurrentUser, get_current_user

_DEFAULT_USAGE_WINDOW_DAYS = 30

router = APIRouter(prefix="/billing", tags=["billing"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> BillingService:
    return BillingService(BillingRepository(db), RazorpayAdapter())


@router.post("/subscriptions", response_model=SubscriptionRead, status_code=status.HTTP_201_CREATED)
async def create_subscription(
    body: SubscriptionCreateRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[BillingService, Depends(get_service)],
):
    return await service.start_subscription(UUID(user.user_id), body.plan)


@router.get("/usage", response_model=list[UsageSummaryRow])
async def get_usage_summary(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    service: Annotated[BillingService, Depends(get_service)],
    since: datetime | None = Query(None),
):
    """M7 usage dashboard (TechnicalDesign.md §8 rule 11) — current user
    only; no channel_id involved, so no separate ownership check needed."""
    window_start = since or (datetime.now(UTC) - timedelta(days=_DEFAULT_USAGE_WINDOW_DAYS))
    return await service.get_usage_summary(UUID(user.user_id), window_start.date())


@router.post("/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def razorpay_webhook(request: Request):
    signature = request.headers.get("X-Razorpay-Signature", "")
    body = await request.body()
    if not RazorpayAdapter().verify_webhook_signature(body, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    # Lazy import: billing_tasks imports BillingRepository/Service, which
    # triggers this package's __init__.py (-> this router) — a top-level
    # import here would be circular.
    from app.tasks.billing_tasks import handle_payment_webhook

    payload = await request.json()
    handle_payment_webhook.delay(
        payload["event"], payload["payload"]["subscription"]["entity"]["id"]
    )
