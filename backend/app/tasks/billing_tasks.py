
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.adapters.razorpay_adapter import RazorpayAdapter
from app.config import get_settings
from app.modules.billing.repository import BillingRepository
from app.modules.billing.service import BillingService
from app.shared.events import run_with_event_flush
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.billing_tasks.handle_payment_webhook", bind=True, max_retries=3)
def handle_payment_webhook(self, event_type: str, provider_subscription_id: str) -> None:
    """Async processing for Razorpay webhook events (thin body — logic in BillingService).

    The router only verifies the signature and enqueues this — ARCHITECTURE.md
    §11: "handle_payment_webhook | Razorpay webhook → task".
    """
    try:
        run_with_event_flush(_handle(event_type, provider_subscription_id))
    except Exception as exc:
        raise self.retry(exc=exc) from exc


async def _handle(event_type: str, provider_subscription_id: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            service = BillingService(BillingRepository(session), RazorpayAdapter())
            await service.handle_webhook_event(event_type, provider_subscription_id)
    finally:
        await engine.dispose()
