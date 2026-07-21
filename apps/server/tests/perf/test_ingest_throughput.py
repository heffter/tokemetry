"""Sustained ingest throughput (Task 70.9, NFR-PERF-002, AC-015).

Drives events through the real v2 ingest path (privacy validation, source
resolution, revision engine, upsert) in bounded batches and measures the
sustained rate. Like the query benchmark, this asserts only a loose floor so it
never flakes on varied CI hardware; the reference-hardware figure (>= 1000
events/s sustained) is measured and recorded in
docs/architecture/performance.md.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType, UsageEventV2
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.ingest_v2 import IngestV2Service

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_SOURCE = SourceRef(type=SourceType.GATEWAY, name="proxy", version="1")

#: Loose floor to catch a catastrophic regression on any hardware; the real
#: acceptance figure is measured on reference hardware (see the perf doc).
_MIN_RATE = 100.0


def _event(index: int) -> UsageEventV2:
    return UsageEventV2.model_validate(
        {
            "schema_version": 2,
            "event_id": f"anthropic:tp{index}",
            "event_kind": "attempt",
            "finality": "final",
            "sequence": 1,
            "provider": "anthropic",
            "native_model": "claude-sonnet-4-5",
            "ts_started": _TS,
            "input_tokens": 1000,
            "output_tokens": 300,
            "source": _SOURCE,
        }
    )


async def test_sustained_ingest_throughput(async_session: AsyncSession) -> None:
    service = IngestV2Service(
        async_session, data_quality=DataQualityService(async_session)
    )
    total = 1000
    batch_size = 200
    events = [_event(i) for i in range(total)]

    started = time.perf_counter()
    accepted = 0
    for offset in range(0, total, batch_size):
        result = await service.ingest(events[offset : offset + batch_size])
        await async_session.commit()
        accepted += result.accepted
    elapsed = time.perf_counter() - started

    assert accepted == total  # every event landed exactly once
    rate = total / elapsed if elapsed > 0 else float("inf")
    # Reported for the perf doc; only a loose floor is asserted here.
    print(f"\ningest throughput: {rate:.0f} events/s ({total} in {elapsed:.2f}s)")
    assert rate > _MIN_RATE, f"ingest throughput {rate:.0f}/s below floor"
