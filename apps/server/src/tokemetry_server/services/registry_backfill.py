"""Historical registry backfill (provider-neutral v2, TOK-2, subtask 61.6).

Populates the provider and model registries from data already stored in
``usage_events`` and ``limit_snapshots``, so a database that predates the
registries gets accurate rows without re-ingesting. Runs once at startup,
guarded by an ``app_settings`` marker, and never mutates usage rows
(FR-MODEL-007). A ``--force`` re-run (see the CLI) reconciles the registries
after a recovery without duplicating rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.registries import ProviderRegistryService

#: app_settings key marking that the one-time registry backfill has completed.
BACKFILL_MARKER_KEY = "registry_backfill_done"

_ACTIVE = "active"
_UNKNOWN = "unknown"


def _is_known_claude_model(provider: str, native_model: str) -> bool:
    """Whether ``native_model`` belongs to a recognized Claude model family.

    Dated (``claude-3-5-sonnet-20241022``) and undated (``claude-fable-5``)
    Claude ids both start with ``claude``; nothing else is recognized here, so
    every other model is backfilled as ``unknown`` for later cataloguing.
    """
    return provider == "anthropic" and native_model.lower().startswith("claude")


def _as_utc(value: datetime | None) -> datetime | None:
    """Coerce a possibly-naive stored timestamp to UTC (SQLite drops tzinfo)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _min_ts(a: datetime | None, b: datetime | None) -> datetime | None:
    """Earlier of two timestamps, tolerating naive/aware and None."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _as_utc(a) <= _as_utc(b) else b  # type: ignore[operator]


def _max_ts(a: datetime | None, b: datetime | None) -> datetime | None:
    """Later of two timestamps, tolerating naive/aware and None."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _as_utc(a) >= _as_utc(b) else b  # type: ignore[operator]


@dataclass(frozen=True)
class BackfillResult:
    """Summary of a backfill run."""

    skipped: bool
    providers: int
    models_active: int
    models_unknown: int


class RegistryBackfill:
    """One-time, idempotent registry backfill from historical usage data."""

    def __init__(self, session: AsyncSession, dedup_window_seconds: float = 3600.0) -> None:
        """Create the backfill bound to ``session`` (caller owns the txn)."""
        self._session = session
        self._providers = ProviderRegistryService(session)
        self._data_quality = DataQualityService(session, dedup_window_seconds)

    async def run(self, *, force: bool = False) -> BackfillResult:
        """Backfill the registries, unless already done and not ``force``."""
        if not force and await self._already_done():
            return BackfillResult(True, 0, 0, 0)

        providers = await self._backfill_providers()
        active, unknown = await self._backfill_models()
        await self._set_marker()
        return BackfillResult(False, providers, active, unknown)

    async def _already_done(self) -> bool:
        return await self._session.get(models.AppSetting, BACKFILL_MARKER_KEY) is not None

    async def _set_marker(self) -> None:
        now = datetime.now(UTC)
        existing = await self._session.get(models.AppSetting, BACKFILL_MARKER_KEY)
        if existing is None:
            self._session.add(
                models.AppSetting(key=BACKFILL_MARKER_KEY, value=now.isoformat(), updated_at=now)
            )
        else:
            existing.value = now.isoformat()
            existing.updated_at = now

    async def _distinct_providers(self) -> set[str]:
        event_providers = (
            await self._session.execute(select(models.UsageEvent.provider).distinct())
        ).scalars().all()
        limit_providers = (
            await self._session.execute(select(models.LimitSnapshot.provider).distinct())
        ).scalars().all()
        return set(event_providers) | set(limit_providers)

    async def _backfill_providers(self) -> int:
        providers = sorted(await self._distinct_providers())
        for provider in providers:
            await self._providers.resolve(provider, "accept")
        return len(providers)

    async def _backfill_models(self) -> tuple[int, int]:
        rows = (
            await self._session.execute(
                select(
                    models.UsageEvent.provider,
                    models.UsageEvent.model,
                    func.min(models.UsageEvent.ts),
                    func.max(models.UsageEvent.ts),
                ).group_by(models.UsageEvent.provider, models.UsageEvent.model)
            )
        ).all()

        active = 0
        unknown = 0
        for provider, native_model, first_seen, last_seen in rows:
            known = _is_known_claude_model(provider, native_model)
            lifecycle = _ACTIVE if known else _UNKNOWN
            await self._upsert_model(provider, native_model, lifecycle, first_seen, last_seen)
            if known:
                active += 1
            else:
                unknown += 1
                await self._data_quality.record_safe(
                    "unknown_model",
                    f"{provider}/{native_model}",
                    last_seen,
                    detail={
                        "provider": provider,
                        "native_model": native_model,
                        "source": "backfill",
                    },
                )
        return active, unknown

    async def _upsert_model(
        self,
        provider: str,
        native_model: str,
        lifecycle: str,
        first_seen: datetime | None,
        last_seen: datetime | None,
    ) -> None:
        existing = await self._session.get(models.Model, (provider, native_model))
        if existing is None:
            self._session.add(
                models.Model(
                    provider=provider,
                    native_model_id=native_model,
                    lifecycle=lifecycle,
                    capabilities={},
                    first_seen=first_seen,
                    last_seen=last_seen,
                )
            )
            return
        # Extend the observed window; upgrade unknown -> active, never downgrade.
        existing.first_seen = _min_ts(existing.first_seen, first_seen)
        existing.last_seen = _max_ts(existing.last_seen, last_seen)
        if lifecycle == _ACTIVE and existing.lifecycle == _UNKNOWN:
            existing.lifecycle = _ACTIVE
