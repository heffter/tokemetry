"""Integration tests for RevisionEngine against the v2 ledger."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.revisions import ConflictMode, Outcome, RevisionEngine

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> UsageEventV2:
    defaults: dict[str, object] = {
        "schema_version": 2,
        "event_id": "anthropic:req_1",
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


async def _active(session: AsyncSession) -> models.UsageEventV2 | None:
    return await session.get(models.UsageEventV2, ("anthropic", "anthropic:req_1"))


async def _revisions(session: AsyncSession) -> list[models.UsageEventRevision]:
    rows = await session.execute(sa.select(models.UsageEventRevision))
    return list(rows.scalars().all())


async def test_new_event_accepted(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    outcome = await engine.apply(_event())
    await async_session.commit()
    assert outcome is Outcome.ACCEPTED
    row = await _active(async_session)
    assert row is not None
    assert row.output_tokens == 100


async def test_identical_replay_is_duplicate(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    await engine.apply(_event())
    await async_session.commit()
    outcome = await engine.apply(_event())
    await async_session.commit()
    assert outcome is Outcome.DUPLICATE
    assert not await _revisions(async_session)


async def test_higher_snapshot_supersedes_and_archives(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    await engine.apply(_event(finality="snapshot", sequence=1, output_tokens=50))
    await async_session.commit()
    outcome = await engine.apply(_event(finality="snapshot", sequence=2, output_tokens=80))
    await async_session.commit()
    assert outcome is Outcome.UPDATED
    row = await _active(async_session)
    assert row is not None
    assert row.sequence == 2
    assert row.output_tokens == 80
    revisions = await _revisions(async_session)
    assert len(revisions) == 1
    assert revisions[0].reason == "superseded"
    assert revisions[0].sequence == 1


async def test_final_supersedes_snapshot(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    await engine.apply(_event(finality="snapshot", sequence=1))
    await async_session.commit()
    outcome = await engine.apply(_event(finality="final", sequence=1, output_tokens=120))
    await async_session.commit()
    assert outcome is Outcome.UPDATED
    row = await _active(async_session)
    assert row is not None
    assert row.finality == "final"


async def test_same_sequence_conflict_rejected_and_recorded(
    async_session: AsyncSession,
) -> None:
    dq = DataQualityService(async_session)
    engine = RevisionEngine(async_session, data_quality=dq)
    await engine.apply(_event(finality="snapshot", sequence=1, output_tokens=50))
    await async_session.commit()
    outcome = await engine.apply(_event(finality="snapshot", sequence=1, output_tokens=70))
    await async_session.commit()
    assert outcome is Outcome.REJECTED
    row = await _active(async_session)
    assert row is not None
    assert row.output_tokens == 50  # unchanged
    open_events = await dq.open_events("sequence_conflict")
    assert len(open_events) == 1
    assert open_events[0].subject == "anthropic/anthropic:req_1"


async def test_snapshot_after_final_is_ignored(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    await engine.apply(_event(finality="final", sequence=1, output_tokens=100))
    await async_session.commit()
    outcome = await engine.apply(_event(finality="snapshot", sequence=9, output_tokens=5))
    await async_session.commit()
    assert outcome is Outcome.DUPLICATE
    row = await _active(async_session)
    assert row is not None
    assert row.finality == "final"
    assert row.output_tokens == 100


async def test_final_over_final_without_correction_rejected(
    async_session: AsyncSession,
) -> None:
    dq = DataQualityService(async_session)
    engine = RevisionEngine(async_session, data_quality=dq)
    await engine.apply(_event(finality="final", sequence=1, output_tokens=100))
    await async_session.commit()
    outcome = await engine.apply(_event(finality="final", sequence=2, output_tokens=200))
    await async_session.commit()
    assert outcome is Outcome.REJECTED
    row = await _active(async_session)
    assert row is not None
    assert row.output_tokens == 100  # unchanged
    assert len(await dq.open_events("sequence_conflict")) == 1


async def test_final_over_final_correction_audited(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    await engine.apply(_event(finality="final", sequence=1, output_tokens=100))
    await async_session.commit()
    outcome = await engine.apply(
        _event(finality="final", sequence=2, output_tokens=250),
        correction=True,
        actor="admin@example.com",
        reason_text="provider re-billed the request",
    )
    await async_session.commit()
    assert outcome is Outcome.CORRECTED
    row = await _active(async_session)
    assert row is not None
    assert row.output_tokens == 250
    revisions = await _revisions(async_session)
    assert len(revisions) == 1
    revision = revisions[0]
    assert revision.reason == "correction"
    assert revision.actor == "admin@example.com"
    assert revision.payload["reason_text"] == "provider re-billed the request"
    assert revision.payload["previous"]["output_tokens"] == 100


async def test_keep_max_mode_matches_v1(async_session: AsyncSession) -> None:
    engine = RevisionEngine(async_session)
    await engine.apply(
        _event(finality="final", output_tokens=100), mode=ConflictMode.KEEP_MAX
    )
    await async_session.commit()
    # Lower output is dropped (keep-max), higher output wins, none archived.
    low = await engine.apply(
        _event(finality="final", output_tokens=40), mode=ConflictMode.KEEP_MAX
    )
    high = await engine.apply(
        _event(finality="final", output_tokens=300), mode=ConflictMode.KEEP_MAX
    )
    await async_session.commit()
    assert low is Outcome.DUPLICATE
    assert high is Outcome.UPDATED
    row = await _active(async_session)
    assert row is not None
    assert row.output_tokens == 300
    assert not await _revisions(async_session)
