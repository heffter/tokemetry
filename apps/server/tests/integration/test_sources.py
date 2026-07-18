"""Integration tests for the source registry and auto-registration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.ingest_v2 import IngestV2Service
from tokemetry_server.services.sources import SourceRegistryService

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 7, 11, 12, 0, 0, tzinfo=UTC)


def _source(**overrides: Any) -> SourceRef:
    defaults: dict[str, Any] = {
        "type": SourceType.GATEWAY,
        "name": "aiProviderProxy",
        "version": "1.0.0",
        "instance_id": "proxy-01",
    }
    defaults.update(overrides)
    return SourceRef.model_validate(defaults)


async def test_resolve_or_create_is_idempotent(async_session: AsyncSession) -> None:
    service = SourceRegistryService(async_session)
    first = await service.resolve_or_create(_source(), _TS)
    await async_session.commit()
    second = await service.resolve_or_create(_source(), _TS)
    await async_session.commit()
    assert first == second
    count = await async_session.scalar(sa.select(sa.func.count()).select_from(models.Source))
    assert count == 1


async def test_version_and_last_seen_advance(async_session: AsyncSession) -> None:
    service = SourceRegistryService(async_session)
    source_id = await service.resolve_or_create(_source(version="1.0.0"), _TS)
    await async_session.commit()
    await service.resolve_or_create(_source(version="1.1.0"), _LATER)
    await async_session.commit()

    row = await async_session.get(models.Source, source_id)
    assert row is not None
    assert row.version == "1.1.0"
    assert row.last_seen.replace(tzinfo=None) == _LATER.replace(tzinfo=None)
    assert row.billing_mode == "api_billed"


async def test_null_instance_id_not_duplicated(async_session: AsyncSession) -> None:
    service = SourceRegistryService(async_session)
    a = await service.resolve_or_create(_source(instance_id=None), _TS)
    await async_session.commit()
    b = await service.resolve_or_create(_source(instance_id=None), _TS)
    await async_session.commit()
    assert a == b


async def test_two_sources_one_machine_coexist(async_session: AsyncSession) -> None:
    """One machine may host several sources (FR-SOURCE-009)."""
    service = SourceRegistryService(async_session)
    collector = await service.resolve_or_create(
        _source(type=SourceType.COLLECTOR, name="collector", instance_id=None),
        _TS,
        machine="devbox-01",
    )
    sdk = await service.resolve_or_create(
        _source(type=SourceType.SDK, name="python-sdk", instance_id=None),
        _TS,
        machine="devbox-01",
    )
    await async_session.commit()
    assert collector != sdk
    result = await async_session.execute(
        sa.select(models.Source).where(models.Source.machine == "devbox-01")
    )
    assert len(result.scalars().all()) == 2


async def test_revocation_never_deletes_history(async_session: AsyncSession) -> None:
    """Revoking a source keeps the row and its attributed events (FR-SOURCE-012)."""
    service = SourceRegistryService(async_session)
    source_id = await service.resolve_or_create(_source(), _TS)
    await async_session.commit()
    row = await async_session.get(models.Source, source_id)
    assert row is not None
    row.revoked = True
    await async_session.commit()

    still_there = await async_session.get(models.Source, source_id)
    assert still_there is not None
    assert still_there.revoked is True


async def test_v2_ingest_auto_registers_source(async_session: AsyncSession) -> None:
    event = UsageEventV2.model_validate(
        {
            "schema_version": 2,
            "event_id": "anthropic:req_1",
            "event_kind": "attempt",
            "finality": "final",
            "sequence": 1,
            "provider": "anthropic",
            "native_model": "claude-sonnet-4-5",
            "ts_started": _TS,
            "machine": "devbox-01",
            "output_tokens": 100,
            "source": _source(),
        }
    )
    await IngestV2Service(async_session).ingest([event], token_label="proxy-token")
    await async_session.commit()

    source = (await async_session.execute(sa.select(models.Source))).scalar_one()
    assert source.name == "aiProviderProxy"
    assert source.token_label == "proxy-token"
    ledger = await async_session.get(models.UsageEventV2, ("anthropic", "anthropic:req_1"))
    assert ledger is not None
    assert ledger.source_id == source.id
