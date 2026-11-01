import os
import tempfile

# Test configuration must be in the environment *before* the app modules are imported,
# because settings and the engine are created lazily and cached.
# A temporary SQLite *file* (not :memory:) so concurrent requests each get their own connection.
_TEST_DB = os.path.join(tempfile.mkdtemp(prefix="txn-webhook-tests-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB}"
os.environ["PROCESSING_DELAY_SECONDS"] = "0"
os.environ["PROCESSING_TRIGGER"] = "inline"
os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["AUTO_CREATE_TABLES"] = "false"

from collections.abc import AsyncIterator  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app.db import get_session_factory, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Transaction  # noqa: E402


@pytest.fixture(autouse=True)
async def _clean_database() -> AsyncIterator[None]:
    await init_db()
    async with get_session_factory()() as session:
        await session.execute(delete(Transaction))
        await session.commit()
    yield


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def webhook_payload() -> dict:
    return {
        "transaction_id": "txn_abc123def456",
        "source_account": "acc_user_789",
        "destination_account": "acc_merchant_456",
        "amount": 1500,
        "currency": "INR",
    }
