"""Integration tests for the transactional v2 ingest service and request id."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.ingest_v2 import (
    BatchValidationError,
    IngestV2Service,
)

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _event(event_id: str = "anthropic:req_1", **overrides: object) -> UsageEventV2:
    defaults: dict[str, object] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": _TS,
        "output_tokens": 100,
        "source": SourceRef(type=SourceType.GATEWAY, name="proxy", version="1"),
    }
    defaults.update(overrides)
    return UsageEventV2.model_validate(defaults)


def _service(session: AsyncSession, *, max_returned_ids: int | None = None) -> IngestV2Service:
    if max_returned_ids is None:
        return IngestV2Service(session, data_quality=DataQualityService(session))
    return IngestV2Service(
        session, data_quality=DataQualityService(session), max_returned_ids=max_returned_ids
    )


async def _count(session: AsyncSession, model: type) -> int:
    return await session.scalar(sa.select(sa.func.count()).select_from(model)) or 0


async def test_new_batch_all_accepted_and_ledger_written(
    async_session: AsyncSession,
) -> None:
    service = _service(async_session)
    result = await service.ingest(
        [_event("anthropic:a"), _event("anthropic:b")],
        token_label="proxy-token",
        request_id="req-xyz",
    )
    await async_session.commit()
    assert result.accepted == 2
    assert result.batch_id
    assert await _count(async_session, models.UsageEventV2) == 2

    batches = list(
        (await async_session.execute(sa.select(models.IngestBatch))).scalars().all()
    )
    assert len(batches) == 1
    assert batches[0].batch_id == result.batch_id
    assert batches[0].accepted == 2
    assert batches[0].schema_version == 2
    assert batches[0].token_label == "proxy-token"
    assert batches[0].request_id == "req-xyz"


async def test_mixed_batch_counts(async_session: AsyncSession) -> None:
    """New, in-batch replay, and an in-batch snapshot upgrade all count."""
    service = _service(async_session)
    result = await service.ingest(
        [
            _event("anthropic:a"),  # accepted
            _event("anthropic:a"),  # identical replay -> duplicate
            _event("anthropic:b", finality="snapshot", sequence=1, output_tokens=10),
            _event("anthropic:b", finality="snapshot", sequence=2, output_tokens=20),
        ]
    )
    await async_session.commit()
    assert result.accepted == 2
    assert result.duplicate == 1
    assert result.updated == 1


async def test_conflict_counts_rejected_but_batch_commits(
    async_session: AsyncSession,
) -> None:
    dq = DataQualityService(async_session)
    service = IngestV2Service(async_session, data_quality=dq)
    result = await service.ingest(
        [
            _event("anthropic:c", finality="snapshot", sequence=1, output_tokens=10),
            _event("anthropic:c", finality="snapshot", sequence=1, output_tokens=20),
        ]
    )
    await async_session.commit()
    assert result.accepted == 1
    assert result.rejected == 1
    assert len(await dq.open_events("sequence_conflict")) == 1
    # The batch still committed its accepted event and its ledger row.
    assert await _count(async_session, models.UsageEventV2) == 1
    assert await _count(async_session, models.IngestBatch) == 1


async def test_privacy_failure_rejects_whole_batch(
    async_session: AsyncSession,
) -> None:
    service = _service(async_session)
    with pytest.raises(BatchValidationError) as exc:
        await service.ingest(
            [
                _event("anthropic:ok"),
                _event("anthropic:bad", extra={"anthropic": {"prompt": "secret"}}),
            ]
        )
    issues = exc.value.issues
    assert any(i.index == 1 and i.code == "content_key" for i in issues)
    # Atomic: nothing was persisted, not even the valid event or a batch row.
    assert await _count(async_session, models.UsageEventV2) == 0
    assert await _count(async_session, models.IngestBatch) == 0


async def test_return_ids_optional(async_session: AsyncSession) -> None:
    service = _service(async_session)
    without = await service.ingest([_event("anthropic:x")])
    assert without.accepted_ids == []

    withids = await service.ingest([_event("anthropic:y")], return_ids=True)
    assert withids.accepted_ids == ["anthropic:y"]


async def test_returned_ids_capped(async_session: AsyncSession) -> None:
    service = _service(async_session, max_returned_ids=2)
    events = [_event(f"anthropic:e{i}") for i in range(5)]
    result = await service.ingest(events, return_ids=True)
    assert result.accepted == 5
    assert len(result.accepted_ids) == 2
    assert result.ids_truncated is True


def test_response_carries_request_id(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.headers.get("X-Request-ID")


def test_client_request_id_is_echoed(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})
    assert response.headers["X-Request-ID"] == "trace-123"
