"""Database models."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TransactionStatus(StrEnum):
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The UNIQUE constraint is what makes the webhook idempotent: a second insert with the
    # same transaction_id fails at the database level, so there is no race between checks.
    transaction_id: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    source_account: Mapped[str] = mapped_column(String(128), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TransactionStatus.PROCESSING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Transaction {self.transaction_id} {self.status}>"
