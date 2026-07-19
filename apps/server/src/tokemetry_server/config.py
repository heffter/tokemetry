"""Server configuration loaded from the environment.

Settings come from ``TOKEMETRY_``-prefixed environment variables (or an
``.env`` file for development). The database URL uses an async driver; the
sync variant needed by Alembic is derived on demand.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    #: How ingest treats an event whose provider is not a known/registered one
    #: (FR-PROVIDER-005). "accept" stores the event and records the provider as
    #: unregistered; "reject" refuses the batch. Default accepts so a new
    #: provider is never silently lost before its adapter is registered.
    registry_unknown_provider_policy: Literal["accept", "reject"] = Field(default="accept")

    #: Window over which repeated data-quality anomalies of the same kind and
    #: subject collapse onto a single open record (seconds). Larger values mean
    #: fewer rows for a persistent issue; the default is one hour.
    data_quality_dedup_window_seconds: float = Field(default=3600.0, gt=0)

    #: Maximum events accepted in one v2 ingest batch (FR-INGEST-005).
    ingest_max_events: int = Field(default=1000, ge=1)
    #: Maximum decompressed byte size of a v2 ingest batch body (FR-INGEST-005).
    ingest_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1)

    #: v2 privacy policy knobs (task 62.2; D-004/D-005). ``strip`` removes
    #: content-like keys instead of rejecting the batch.
    privacy_mode: Literal["reject", "strip"] = Field(default="reject")
    #: Accept the optional tool-name histogram (default off, D-005).
    privacy_tool_names_enabled: bool = Field(default=False)
    #: Maximum serialized size of a single v2 event in bytes (FR-EVENT-028).
    privacy_max_event_bytes: int = Field(default=32 * 1024, ge=1)
    #: Maximum JSON nesting depth of a single v2 event (NFR-SEC-004).
    privacy_max_json_depth: int = Field(default=8, ge=1)
    #: Comma-separated allowlist of permitted dimension keys (D-004).
    privacy_dimension_allowlist: str = Field(default="team,cost_center,environment")

    #: In-process rate-limit buckets, separate for ingest and query traffic
    #: (FR-INGEST-015). ``capacity`` is the burst size; ``per_second`` refills.
    #: A simple token bucket; hardened in Task 70.
    ingest_rate_capacity: float = Field(default=240.0, gt=0)
    ingest_rate_per_second: float = Field(default=120.0, gt=0)
    query_rate_capacity: float = Field(default=240.0, gt=0)
    query_rate_per_second: float = Field(default=120.0, gt=0)

    #: Source-health thresholds (task 63.2, FR-SOURCE-005/006). A source is
    #: stale when its last successful ingest is older than the per-type
    #: threshold; the error window bounds the recent-error rolling count; a
    #: clock skew beyond the warn threshold records a data-quality event.
    source_stale_collector_seconds: float = Field(default=1800.0, gt=0)
    source_stale_gateway_seconds: float = Field(default=600.0, gt=0)
    source_stale_default_seconds: float = Field(default=1800.0, gt=0)
    source_error_window_seconds: float = Field(default=3600.0, gt=0)
    source_clock_skew_warn_seconds: float = Field(default=300.0, gt=0)

    @property
    def project_root_markers(self) -> tuple[str, ...]:
        """Parse :attr:`project_roots` into a tuple of marker segments."""
        return tuple(part.strip() for part in self.project_roots.split(",") if part.strip())

    @property
    def privacy_dimension_allowlist_set(self) -> frozenset[str]:
        """Parse :attr:`privacy_dimension_allowlist` into a set of keys."""
        return frozenset(
            part.strip()
            for part in self.privacy_dimension_allowlist.split(",")
            if part.strip()
        )

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
