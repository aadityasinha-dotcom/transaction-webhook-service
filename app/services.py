"""Business logic: idempotent intake and background processing of transactions."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Transaction, TransactionStatus
from app.schemas import TransactionWebhook

logger = logging.getLogger(__name__)


class TransactionNotFound(Exception):
    pass


async def get_transaction(session: AsyncSession, transaction_id: str) -> Transaction | None:
    result = await session.execute(select(Transaction).where(Transaction.transaction_id == transaction_id))
    return result.scalar_one_or_none()


async def record_webhook(session: AsyncSession, payload: TransactionWebhook) -> tuple[Transaction, bool]:
    """Persist the webhook exactly once.

    Returns (transaction, created). A single `INSERT ... ON CONFLICT DO NOTHING RETURNING`
    relies on the UNIQUE constraint on transaction_id, so concurrent duplicate webhooks can
    never both be inserted and the intake costs one round trip to the database.
    """
    values = dict(
        transaction_id=payload.transaction_id,
        source_account=payload.source_account,
        destination_account=payload.destination_account,
        amount=payload.amount,
        currency=payload.currency,
        status=TransactionStatus.PROCESSING.value,
        created_at=datetime.now(UTC),
        processing_attempts=0,
    )
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    statement = (
        insert(Transaction)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Transaction.transaction_id])
        .returning(Transaction)
    )
    transaction = (await session.execute(statement)).scalar_one_or_none()
    await session.commit()

    if transaction is not None:
        logger.info("Accepted transaction %s", payload.transaction_id)
        return transaction, True

    existing = await get_transaction(session, payload.transaction_id)
    if existing is None:  # pragma: no cover - only if the row vanished between the two statements
        raise RuntimeError(f"Transaction {payload.transaction_id} conflicted but cannot be read back")
    logger.info("Duplicate webhook ignored for %s", payload.transaction_id)
    return existing, False


PROCESSING_LEASE_SECONDS = 90  # a claim older than this is considered dead and may be retried


async def process_transaction(
    session_factory: async_sessionmaker[AsyncSession],
    transaction_id: str,
    delay_seconds: float,
) -> str:
    """Simulate the slow external call, then mark the transaction PROCESSED.

    Returns "processed", "in_progress" (another invocation holds the lease) or
    "already_processed". Raises TransactionNotFound for unknown ids.

    Exactly-once is enforced in two places:
      1. A *claim* before the slow work: one atomic UPDATE sets processing_started_at only if the
         row is still PROCESSING and not already claimed (or the claim is older than the lease).
         Duplicate deliveries therefore return immediately instead of sleeping 30 s.
      2. A conditional UPDATE after the work: only a PROCESSING row can become PROCESSED.
    The DB connection is *not* held during the delay so a burst of transactions cannot exhaust
    the connection pool. If the process dies mid-sleep, the lease expires and the retry job
    re-triggers processing.
    """
    now = datetime.now(UTC)
    async with session_factory() as session:
        transaction = await get_transaction(session, transaction_id)
        if transaction is None:
            raise TransactionNotFound(transaction_id)
        if transaction.status == TransactionStatus.PROCESSED.value:
            return "already_processed"
        row_id = transaction.id

        lease_cutoff = now - timedelta(seconds=PROCESSING_LEASE_SECONDS)
        claim = await session.execute(
            update(Transaction)
            .where(
                Transaction.id == row_id,
                Transaction.status == TransactionStatus.PROCESSING.value,
                or_(Transaction.processing_started_at.is_(None), Transaction.processing_started_at < lease_cutoff),
            )
            .values(processing_started_at=now, processing_attempts=Transaction.processing_attempts + 1)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    if claim.rowcount == 0:
        logger.info("Transaction %s is already being processed; ignoring duplicate trigger", transaction_id)
        return "in_progress"

    try:
        await asyncio.sleep(delay_seconds)  # stand-in for external API calls
    except Exception as exc:  # pragma: no cover - defensive; recorded so the retry job can see it
        await _record_failure(session_factory, row_id, exc)
        raise

    async with session_factory() as session:
        result = await session.execute(
            update(Transaction)
            .where(Transaction.id == row_id, Transaction.status == TransactionStatus.PROCESSING.value)
            .values(status=TransactionStatus.PROCESSED.value, processed_at=datetime.now(UTC), last_error=None)
        )
        await session.commit()

    if result.rowcount == 0:  # pragma: no cover - only if someone processed it despite our lease
        return "already_processed"
    logger.info("Processed transaction %s", transaction_id)
    return "processed"


async def _record_failure(session_factory: async_sessionmaker[AsyncSession], row_id: int, exc: Exception) -> None:
    async with session_factory() as session:
        await session.execute(
            update(Transaction)
            .where(Transaction.id == row_id)
            .values(processing_started_at=None, last_error=str(exc)[:2000])
        )
        await session.commit()
