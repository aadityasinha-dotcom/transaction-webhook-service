from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import TransactionOut
from app.services import get_transaction

router = APIRouter(prefix="/v1/transactions", tags=["transactions"])


@router.get(
    "/{transaction_id}",
    response_model=list[TransactionOut],
    summary="Look up a transaction's status (for testing)",
    responses={404: {"description": "Unknown transaction_id"}},
)
async def get_transaction_status(
    transaction_id: str, session: AsyncSession = Depends(get_session)
) -> list[TransactionOut]:
    # The assessment specifies a list response, so a single match is returned as a one-item list.
    transaction = await get_transaction(session, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    return [TransactionOut.model_validate(transaction)]
