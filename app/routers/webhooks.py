import logging

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session, get_session_factory
from app.models import TransactionStatus
from app.schemas import TransactionWebhook, WebhookAccepted
from app.services import process_transaction, record_webhook

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


async def _process_in_background(transaction_id: str, delay_seconds: float) -> None:
    try:
        await process_transaction(get_session_factory(), transaction_id, delay_seconds)
    except Exception:  # never let a background failure surface as an unhandled error
        logger.exception("Background processing failed for %s", transaction_id)


@router.post(
    "/transactions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=WebhookAccepted,
    summary="Receive a transaction webhook",
)
async def receive_transaction_webhook(
    payload: TransactionWebhook,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WebhookAccepted:
    """Acknowledge immediately with 202; the 30s processing never happens inside this request.

    Duplicate `transaction_id`s are accepted (still 202) but stored and processed only once.
    """
    transaction, created = await record_webhook(session, payload)

    # In "supabase" mode a Postgres AFTER INSERT trigger calls /v1/internal/process, so nothing
    # else to do here. In "inline" mode (local dev / long-lived servers) schedule it ourselves.
    if settings.processing_trigger == "inline" and created and transaction.status == TransactionStatus.PROCESSING:
        background_tasks.add_task(_process_in_background, transaction.transaction_id, settings.processing_delay_seconds)

    return WebhookAccepted(transaction_id=transaction.transaction_id)
