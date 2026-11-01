"""FastAPI application entrypoint (Vercel loads the `app` object from this file)."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import dispose_engine, init_db
from app.routers import health, internal, transactions, webhooks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().auto_create_tables:
        await init_db()
    yield
    await dispose_engine()


app = FastAPI(
    title="Transaction Webhook Service",
    version="1.0.0",
    description=(
        "Receives payment-processor webhooks, acknowledges them within milliseconds and "
        "processes each transaction exactly once in the background."
    ),
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(transactions.router)
app.include_router(internal.router)
