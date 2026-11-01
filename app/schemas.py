"""Request / response schemas."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


def to_iso_z(value: datetime | None) -> str | None:
    """Render timestamps as `2024-01-15T10:30:00Z` (UTC, no offset suffix)."""
    if value is None:
        return None
    if value.tzinfo is None:  # SQLite drops tzinfo; everything we store is UTC
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TransactionWebhook(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    transaction_id: str = Field(min_length=1, max_length=128, examples=["txn_abc123def456"])
    source_account: str = Field(min_length=1, max_length=128, examples=["acc_user_789"])
    destination_account: str = Field(min_length=1, max_length=128, examples=["acc_merchant_456"])
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2, examples=[1500])
    currency: str = Field(min_length=3, max_length=3, examples=["INR"])

    @field_validator("currency")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class WebhookAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"
    transaction_id: str


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transaction_id: str
    source_account: str
    destination_account: str
    amount: Decimal
    currency: str
    status: Literal["PROCESSING", "PROCESSED"]
    created_at: datetime
    processed_at: datetime | None

    @field_serializer("amount")
    def _amount(self, value: Decimal) -> float:
        return float(value)

    @field_serializer("created_at", "processed_at")
    def _timestamps(self, value: datetime | None) -> str | None:
        return to_iso_z(value)


class HealthOut(BaseModel):
    status: Literal["HEALTHY"] = "HEALTHY"
    current_time: str


class ProcessRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=128)


class ProcessResult(BaseModel):
    transaction_id: str
    outcome: Literal["processed", "in_progress", "already_processed"]
