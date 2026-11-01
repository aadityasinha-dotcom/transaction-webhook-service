import asyncio
import re
import time

import pytest
from sqlalchemy import func, select

from app.db import get_session_factory
from app.models import Transaction
from app.schemas import TransactionWebhook
from app.services import process_transaction, record_webhook

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


async def _count_rows() -> int:
    async with get_session_factory()() as session:
        return await session.scalar(select(func.count()).select_from(Transaction))


async def test_health_check(client):
    response = await client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "HEALTHY"
    assert ISO_Z.match(body["current_time"])


async def test_webhook_returns_202_quickly(client, webhook_payload):
    started = time.perf_counter()
    response = await client.post("/v1/webhooks/transactions", json=webhook_payload)
    elapsed = time.perf_counter() - started

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "transaction_id": "txn_abc123def456"}
    assert elapsed < 0.5


async def test_single_transaction_is_processed(client, webhook_payload):
    await client.post("/v1/webhooks/transactions", json=webhook_payload)

    response = await client.get("/v1/transactions/txn_abc123def456")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and len(body) == 1
    txn = body[0]
    assert txn["transaction_id"] == "txn_abc123def456"
    assert txn["source_account"] == "acc_user_789"
    assert txn["destination_account"] == "acc_merchant_456"
    assert txn["amount"] == 1500.0
    assert txn["currency"] == "INR"
    assert txn["status"] == "PROCESSED"  # delay is 0 in tests, so the background task has finished
    assert ISO_Z.match(txn["created_at"])
    assert ISO_Z.match(txn["processed_at"])


async def test_duplicate_webhooks_are_stored_once(client, webhook_payload):
    for _ in range(5):
        response = await client.post("/v1/webhooks/transactions", json=webhook_payload)
        assert response.status_code == 202  # duplicates are acknowledged, never rejected

    assert await _count_rows() == 1
    response = await client.get("/v1/transactions/txn_abc123def456")
    assert response.json()[0]["status"] == "PROCESSED"
    async with get_session_factory()() as session:
        txn = await session.scalar(select(Transaction))
        assert txn.processing_attempts == 1  # processed exactly once


async def test_concurrent_duplicate_webhooks(client, webhook_payload):
    responses = await asyncio.gather(
        *(client.post("/v1/webhooks/transactions", json=webhook_payload) for _ in range(10))
    )
    assert {r.status_code for r in responses} == {202}
    assert await _count_rows() == 1


async def test_processing_is_idempotent_even_if_triggered_twice(webhook_payload):
    factory = get_session_factory()
    async with factory() as session:
        await record_webhook(session, TransactionWebhook(**webhook_payload))

    first, second = await asyncio.gather(
        process_transaction(factory, "txn_abc123def456", 0),
        process_transaction(factory, "txn_abc123def456", 0),
    )
    assert sorted([first, second]) == ["already_processed", "processed"]
    async with factory() as session:
        txn = await session.scalar(select(Transaction))
        assert txn.status == "PROCESSED"
        assert txn.processing_attempts == 1


async def test_processing_waits_for_the_configured_delay(webhook_payload):
    factory = get_session_factory()
    async with factory() as session:
        await record_webhook(session, TransactionWebhook(**webhook_payload))

    started = time.perf_counter()
    outcome = await process_transaction(factory, "txn_abc123def456", 0.2)
    assert outcome == "processed"
    assert time.perf_counter() - started >= 0.2


async def test_unknown_transaction_returns_404(client):
    response = await client.get("/v1/transactions/txn_does_not_exist")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "bad_field",
    [
        {"amount": -5},
        {"amount": "abc"},
        {"currency": "RUPEES"},
        {"transaction_id": ""},
    ],
)
async def test_invalid_webhook_payload_is_rejected(client, webhook_payload, bad_field):
    response = await client.post("/v1/webhooks/transactions", json={**webhook_payload, **bad_field})
    assert response.status_code == 422
    assert await _count_rows() == 0


async def test_internal_process_requires_secret(client, webhook_payload):
    await client.post("/v1/webhooks/transactions", json=webhook_payload)

    response = await client.post("/v1/internal/process", json={"transaction_id": "txn_abc123def456"})
    assert response.status_code == 401

    response = await client.post(
        "/v1/internal/process",
        json={"transaction_id": "txn_abc123def456"},
        headers={"X-Internal-Secret": "wrong"},
    )
    assert response.status_code == 401


async def test_internal_process_endpoint(client, webhook_payload):
    await client.post("/v1/webhooks/transactions", json=webhook_payload)
    headers = {"X-Internal-Secret": "test-secret"}

    # Already processed by the inline background task, so a re-trigger is a harmless no-op.
    response = await client.post("/v1/internal/process", json={"transaction_id": "txn_abc123def456"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"transaction_id": "txn_abc123def456", "outcome": "already_processed"}

    response = await client.post("/v1/internal/process", json={"transaction_id": "txn_missing"}, headers=headers)
    assert response.status_code == 404
