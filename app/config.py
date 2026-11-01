"""Application settings, loaded from environment variables (and a local .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(
        default="sqlite+aiosqlite:///./local.db",
        description=(
            "SQLAlchemy async URL. For Supabase use the *transaction pooler* URI "
            "(port 6543), e.g. postgresql://postgres.<ref>:<password>@aws-0-<region>"
            ".pooler.supabase.com:6543/postgres"
        ),
    )
    processing_delay_seconds: float = Field(
        default=30.0, ge=0, description="Simulated external-API delay applied to every transaction."
    )
    processing_trigger: Literal["inline", "supabase"] = Field(
        default="inline",
        description=(
            "inline: process in a FastAPI background task right after the 202 (local / long-lived "
            "servers). supabase: do nothing after the insert; a Postgres trigger in Supabase calls "
            "POST /v1/internal/process (serverless deployments such as Vercel)."
        ),
    )
    internal_secret: str = Field(
        default="change-me",
        description="Shared secret required in the X-Internal-Secret header of /v1/internal/process.",
    )
    auto_create_tables: bool = Field(
        default=True,
        description=(
            "Run CREATE TABLE IF NOT EXISTS on startup. Disable in production (schema is managed by SQL migrations)."
        ),
    )

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Accept the plain `postgresql://` URI Supabase hands out and make it async-psycopg."""
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value[len("postgresql://") :]
        if value.startswith("postgresql+psycopg://") and "sslmode=" not in value:
            value += ("&" if "?" in value else "?") + "sslmode=require"
        return value

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
