"""Server configuration loaded from the environment.

Settings come from ``TOKEMETRY_``-prefixed environment variables (or an
``.env`` file for development). The database URL uses an async driver; the
sync variant needed by Alembic is derived on demand.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    #: Directory of the built dashboard SPA to serve at the site root. When
    #: unset the server is API-only (the dashboard runs from Vite in dev).
    static_dir: Path | None = Field(default=None)

    #: Monthly subscription price in USD, used to show the "value multiple"
    #: (equivalent API cost vs what the subscription costs). None hides it.
    subscription_monthly_usd: float | None = Field(default=None)

    #: Run the periodic alert evaluation loop in the background.
    alerts_enabled: bool = Field(default=True)
    #: Seconds between background alert evaluations.
    alerts_interval_seconds: float = Field(default=60.0, gt=0)
    #: Seed a default alert-rule set on first run when the table is empty.
    seed_default_alerts: bool = Field(default=True)

    #: IANA timezone name used to evaluate alert quiet hours (e.g.
    #: "Europe/Budapest"). Defaults to UTC; an unknown name falls back to UTC.
    timezone: str = Field(default="UTC")

    #: Comma-separated development-root markers for project grouping: the path
    #: segment after one of these is the project name (e.g. "devel,src,repos").
    project_roots: str = Field(default="devel")

    @property
    def project_root_markers(self) -> tuple[str, ...]:
        """Parse :attr:`project_roots` into a tuple of marker segments."""
        return tuple(part.strip() for part in self.project_roots.split(",") if part.strip())

    # --- Notification channel settings (all optional; a channel is only
    # available when its required settings are present). These are the
    # environment defaults; the UI can override any of them via the
    # ``app_settings`` table (a non-empty stored value wins, blank falls back
    # here). Channel secrets are still never stored on alert_rules rows --
    # rules reference channels by name only. ---
    ntfy_url: str = Field(default="https://ntfy.sh")
    ntfy_topic: str | None = Field(default=None)
    #: Dashboard base URL added as an ntfy Click action so a tapped
    #: notification opens the app (e.g. "http://10.0.0.1:8790").
    dashboard_url: str | None = Field(default=None)
    telegram_bot_token: str | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_user: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_from: str | None = Field(default=None)
    smtp_to: str | None = Field(default=None)
    smtp_use_tls: bool = Field(default=True)

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
