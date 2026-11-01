"""Internal endpoint invoked by the Supabase trigger / retry cron (serverless processing)."""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.schemas import ProcessRequest, ProcessResult
from app.services import TransactionNotFound, process_transaction

router = APIRouter(prefix="/v1/internal", tags=["internal"], include_in_schema=False)


def require_internal_secret(
    x_internal_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if x_internal_secret is None or not secrets.compare_digest(x_internal_secret, settings.internal_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal secret")


@router.post("/process", response_model=ProcessResult, dependencies=[Depends(require_internal_secret)])
async def process(payload: ProcessRequest, settings: Settings = Depends(get_settings)) -> ProcessResult:
    """Run the (slow) processing for one transaction. Safe to call repeatedly."""
    try:
        outcome = await process_transaction(
            get_session_factory(), payload.transaction_id, settings.processing_delay_seconds
        )
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found") from exc
    return ProcessResult(transaction_id=payload.transaction_id, outcome=outcome)
