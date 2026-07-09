"""Server configuration loaded from the environment.

Settings come from ``TOKEMETRY_``-prefixed environment variables (or an
``.env`` file for development). The database URL uses an async driver; the
sync variant needed by Alembic is derived on demand.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Maps async SQLAlchemy drivers to the sync driver Alembic runs with.
_ASYNC_TO_SYNC_DRIVER = {
    "postgresql+asyncpg": "postgresql+psycopg",
    "sqlite+aiosqlite": "sqlite",
}


class Settings(BaseSettings):
    """Runtime configuration for the tokemetry server."""

    model_config = SettingsConfigDict(
        env_prefix="TOKEMETRY_",
        env_file=".env",
        extra="ignore",
    )

    #: Async SQLAlchemy database URL. Defaults to a local SQLite file so the
    #: server and tests run without a Postgres instance; production sets a
    #: ``postgresql+asyncpg://`` URL.
    database_url: str = Field(default="sqlite+aiosqlite:///./tokemetry.db")

    #: Interface the API binds to. In production this is the WireGuard
    #: address so the service is never exposed on the public interface.
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=8787, ge=1, le=65535)

    #: Optional bootstrap bearer token accepted in addition to database
    #: tokens, so the first collector can authenticate before any token has
    #: been minted through the API. Leave unset in steady state.
    api_bootstrap_token: str | None = Field(default=None)

    #: Run Alembic migrations to head on startup. Convenient for a
    #: single-node deployment; disable to manage migrations out of band.
    auto_migrate: bool = Field(default=True)

    #: Monthly subscription price in USD, used to show the "value multiple"
    #: (equivalent API cost vs what the subscription costs). None hides it.
    subscription_monthly_usd: float | None = Field(default=None)

    @property
    def sync_database_url(self) -> str:
        """Return the database URL with a synchronous driver for Alembic."""
        for async_driver, sync_driver in _ASYNC_TO_SYNC_DRIVER.items():
            if self.database_url.startswith(async_driver + ":"):
                return sync_driver + self.database_url[len(async_driver) :]
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
