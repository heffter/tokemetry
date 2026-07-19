"""Tests for the provider and model registry services (TOK-2, subtask 61.3).

Seeding idempotency, alias resolution merging core and DB aliases, the
unknown-provider accept/reject policy, and unknown-model observation lifecycle.
The final case drives a v1 anthropic ingest through the HTTP layer and asserts
the model is observed without disturbing v1 behavior.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.registries import (
    ModelRegistryService,
    ProviderRegistryService,
    ProviderResolution,
    seed_default_providers,
)

_TS = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)

_MACHINE = {"name": "box-1", "platform": "windows", "collector_version": "0.1.0"}


def _utc(value: datetime | None) -> datetime | None:
    """Normalize a stored timestamp to UTC (SQLite returns naive datetimes)."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _event(**overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": "req_1",
        "provider": "anthropic",
        "native_model": "claude-fable-5",
        "ts": "2026-07-09T09:41:14+00:00",
        "session_id": "sess-1",
        "project": "C:\\devel\\tokemetry",
        "input_tokens": 10,
        "output_tokens": 100,
        "cache_read_tokens": 500,
        "cache_write_short_tokens": 0,
        "cache_write_long_tokens": 200,
    }
    event.update(overrides)
    return event


async def _provider_count(session: AsyncSession) -> int:
    return len((await session.execute(select(models.Provider))).scalars().all())


class TestProviderSeeding:
    async def test_seeds_the_three_providers(self, async_session: AsyncSession) -> None:
        inserted = await seed_default_providers(async_session)
        await async_session.commit()
        assert inserted == 3
        ids = {row.id for row in (await async_session.execute(select(models.Provider))).scalars()}
        assert ids == {"anthropic", "openai", "zai"}
        anthropic = await async_session.get(models.Provider, "anthropic")
        assert anthropic is not None
        assert anthropic.registered is True
        assert "claude" in anthropic.aliases
        # FR-LIMIT-012: the seed populates Anthropic's window registry.
        kinds = {w["kind"] for w in anthropic.windows}
        assert {"five_hour", "seven_day"} <= kinds

    async def test_seeding_backfills_windows_on_a_pre_registry_row(
        self, async_session: AsyncSession
    ) -> None:
        # An anthropic row that predates the window registry has empty windows
        # (backfilled by the migration to []); re-seeding fills them in without
        # a data migration, but leaves other fields untouched.
        now = datetime.now(UTC)
        async_session.add(
            models.Provider(
                id="anthropic", display_name="Anthropic", aliases=["claude"],
                pricing_strategy="anthropic", limit_semantics="anthropic_oauth_windows",
                supported_dimensions=["model"], windows=[], registered=True,
                created_at=now, updated_at=now,
            )
        )
        await async_session.commit()

        inserted = await seed_default_providers(async_session)
        await async_session.commit()
        refreshed = await async_session.get(models.Provider, "anthropic")
        assert refreshed is not None
        # anthropic existed (not re-inserted), but its windows are now populated.
        assert inserted == 2  # openai + zai inserted; anthropic backfilled
        assert {w["kind"] for w in refreshed.windows} >= {"five_hour", "seven_day"}

    async def test_seeding_is_idempotent(self, async_session: AsyncSession) -> None:
        await seed_default_providers(async_session)
        await async_session.commit()
        second = await seed_default_providers(async_session)
        await async_session.commit()
        assert second == 0
        assert await _provider_count(async_session) == 3

    async def test_seeding_preserves_edits(self, async_session: AsyncSession) -> None:
        await seed_default_providers(async_session)
        await async_session.commit()
        anthropic = await async_session.get(models.Provider, "anthropic")
        assert anthropic is not None
        anthropic.display_name = "Anthropic (edited)"
        await async_session.commit()

        await seed_default_providers(async_session)
        await async_session.commit()
        refreshed = await async_session.get(models.Provider, "anthropic")
        assert refreshed is not None
        assert refreshed.display_name == "Anthropic (edited)"


class TestProviderNormalization:
    async def test_core_aliases_resolve(self, async_session: AsyncSession) -> None:
        await seed_default_providers(async_session)
        await async_session.commit()
        service = ProviderRegistryService(async_session)
        assert await service.normalize("claude") == "anthropic"
        assert await service.normalize("Z.AI") == "zai"
        assert await service.normalize("codex-cli") == "openai"

    async def test_db_only_alias_resolves(self, async_session: AsyncSession) -> None:
        # A provider whose alias exists only in the DB, not in the core rules.
        async_session.add(
            models.Provider(
                id="acme",
                display_name="Acme",
                aliases=["acme-cli", "acmeai"],
                pricing_strategy="",
                limit_semantics="none",
                supported_dimensions=[],
                registered=True,
                created_at=_TS,
                updated_at=_TS,
            )
        )
        await async_session.commit()
        service = ProviderRegistryService(async_session)
        assert await service.normalize("acmeai") == "acme"
        assert await service.normalize("ACME-CLI") == "acme"

    async def test_unknown_passes_through(self, async_session: AsyncSession) -> None:
        service = ProviderRegistryService(async_session)
        assert await service.normalize("Some-New-Vendor") == "some-new-vendor"


class TestUnknownProviderPolicy:
    async def test_registered_provider_accepted(self, async_session: AsyncSession) -> None:
        await seed_default_providers(async_session)
        await async_session.commit()
        service = ProviderRegistryService(async_session)
        resolution = await service.resolve("claude", "accept")
        assert resolution == ProviderResolution(
            provider="anthropic", accepted=True, registered=True
        )

    async def test_seed_provider_registered_even_without_seeding(
        self, async_session: AsyncSession
    ) -> None:
        # Empty providers table: resolving a known seed still registers it.
        service = ProviderRegistryService(async_session)
        resolution = await service.resolve("openai", "accept")
        await async_session.commit()
        assert resolution.registered is True
        row = await async_session.get(models.Provider, "openai")
        assert row is not None and row.registered is True

    async def test_unknown_accept_marks_unregistered(
        self, async_session: AsyncSession
    ) -> None:
        service = ProviderRegistryService(async_session)
        resolution = await service.resolve("mistral", "accept")
        await async_session.commit()
        assert resolution.accepted is True
        assert resolution.registered is False
        row = await async_session.get(models.Provider, "mistral")
        assert row is not None and row.registered is False

    async def test_unknown_reject_inserts_nothing(
        self, async_session: AsyncSession
    ) -> None:
        service = ProviderRegistryService(async_session)
        resolution = await service.resolve("mistral", "reject")
        await async_session.commit()
        assert resolution.accepted is False
        assert await async_session.get(models.Provider, "mistral") is None


class TestModelObservation:
    async def test_unknown_model_inserted(self, async_session: AsyncSession) -> None:
        service = ModelRegistryService(async_session)
        observation = await service.observe("anthropic", "claude-new", _TS)
        await async_session.commit()
        assert observation.newly_observed is True
        assert observation.lifecycle == "unknown"
        row = await async_session.get(models.Model, ("anthropic", "claude-new"))
        assert row is not None
        assert row.lifecycle == "unknown"
        assert _utc(row.first_seen) == _TS
        assert _utc(row.last_seen) == _TS

    async def test_known_model_advances_last_seen(self, async_session: AsyncSession) -> None:
        service = ModelRegistryService(async_session)
        await service.observe("anthropic", "claude-new", _TS)
        await async_session.commit()
        later = _TS + timedelta(hours=1)
        observation = await service.observe("anthropic", "claude-new", later)
        await async_session.commit()
        assert observation.newly_observed is False
        row = await async_session.get(models.Model, ("anthropic", "claude-new"))
        assert row is not None
        assert _utc(row.last_seen) == later
        assert _utc(row.first_seen) == _TS

    async def test_older_timestamp_does_not_regress(self, async_session: AsyncSession) -> None:
        service = ModelRegistryService(async_session)
        await service.observe("anthropic", "claude-new", _TS)
        await async_session.commit()
        earlier = _TS - timedelta(hours=1)
        await service.observe("anthropic", "claude-new", earlier)
        await async_session.commit()
        row = await async_session.get(models.Model, ("anthropic", "claude-new"))
        assert row is not None
        assert _utc(row.last_seen) == _TS

    async def test_lifecycle_preserved_on_observe(self, async_session: AsyncSession) -> None:
        async_session.add(
            models.Model(
                provider="anthropic",
                native_model_id="claude-active",
                lifecycle="active",
                capabilities={"vision": True},
                first_seen=_TS,
                last_seen=_TS,
            )
        )
        await async_session.commit()
        service = ModelRegistryService(async_session)
        later = _TS + timedelta(hours=2)
        observation = await service.observe("anthropic", "claude-active", later)
        await async_session.commit()
        assert observation.newly_observed is False
        assert observation.lifecycle == "active"
        row = await async_session.get(models.Model, ("anthropic", "claude-active"))
        assert row is not None
        assert row.lifecycle == "active"
        assert row.capabilities == {"vision": True}
        assert _utc(row.last_seen) == later


def test_v1_anthropic_ingest_still_works_and_observes_model(
    client: TestClient, auth: dict[str, str], read_engine: sa.Engine
) -> None:
    """FR-PROVIDER-009: a v1 anthropic event ingests unchanged and is observed."""
    response = client.post(
        "/api/v1/ingest/events",
        json={"machine": _MACHINE, "events": [_event()]},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": 1, "duplicates_merged": 0}

    with read_engine.connect() as conn:
        events = conn.execute(sa.text("SELECT COUNT(*) FROM usage_events")).scalar_one()
        provider = conn.execute(
            sa.text("SELECT registered FROM providers WHERE id = 'anthropic'")
        ).scalar_one()
        model = conn.execute(
            sa.text(
                "SELECT lifecycle FROM models "
                "WHERE provider = 'anthropic' AND native_model_id = 'claude-fable-5'"
            )
        ).scalar_one()
    assert events == 1
    assert bool(provider) is True
    assert model == "unknown"
