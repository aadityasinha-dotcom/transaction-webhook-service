"""Async SQLAlchemy engine / session management."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    url = settings.database_url

    if settings.is_postgres:
        # Supabase's transaction-mode pooler (Supavisor, port 6543) does not support
        # server-side prepared statements, so psycopg must not create them.
        # A small pool is kept because Vercel's Fluid compute reuses function instances.
        return create_async_engine(
            url,
            pool_size=2,
            max_overflow=3,
            pool_pre_ping=True,
            pool_recycle=300,
            connect_args={"prepare_threshold": None},
        )

    # SQLite (local development and tests). An in-memory DB must share one connection.
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url.endswith(":memory:") or url.endswith("sqlite+aiosqlite://"):
        kwargs["poolclass"] = StaticPool
    return create_async_engine(url, **kwargs)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request."""
    async with get_session_factory()() as session:
        yield session


async def init_db() -> None:
    """Create tables that do not exist yet. Used for local dev and tests only."""
    from app import models  # noqa: F401  (register models on Base.metadata)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    await get_engine().dispose()
