"""Deterministic synthetic dataset generator for the benchmark harness (66.8).

Seeds final attempt events spread over a day range across several providers with
high-cardinality sessions and projects (PRD 18.5). Deterministic (no randomness)
so runs are reproducible and comparable; scale via ``count``. Registered gateway
sources give the source dimension realistic values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models

_PROVIDERS = (
    ("anthropic", "claude-sonnet-4-5"),
    ("anthropic", "claude-haiku-4-5"),
    ("openai", "gpt-5"),
    ("zai", "glm-4.6"),
)

#: Cardinality knobs for the synthetic dimensions.
_SESSIONS = 500
_PROJECTS = 50
_MACHINES = 20


async def generate_attempts(
    session: AsyncSession,
    count: int,
    *,
    days: int = 90,
    start: datetime | None = None,
    batch_size: int = 5000,
) -> int:
    """Seed ``count`` final attempt events over ``days``; return the count.

    Events are spread uniformly across the range and cycle through the provider,
    session, project, and machine pools so the group-by and keyset paths see
    realistic cardinality. Flushed in batches to bound memory.
    """
    origin = start if start is not None else datetime(2026, 1, 1, tzinfo=UTC)
    span = timedelta(days=days)
    step = span / max(count, 1)
    for index in range(count):
        provider, native_model = _PROVIDERS[index % len(_PROVIDERS)]
        session.add(
            models.UsageEventV2(
                provider=provider,
                event_id=f"{provider}:evt-{index}",
                schema_version=2,
                event_kind="attempt",
                finality="final",
                sequence=0,
                native_model=native_model,
                ts_started=origin + step * index,
                ts_completed=origin + step * index,
                machine=f"machine-{index % _MACHINES}",
                session_id=f"session-{index % _SESSIONS}",
                project=f"project-{index % _PROJECTS}",
                input_tokens=1000 + (index % 500),
                output_tokens=200 + (index % 100),
                cache_read_tokens=index % 50,
                cache_write_short_tokens=0,
                cache_write_long_tokens=0,
                reasoning_tokens=index % 20,
                success=(index % 17 != 0),
                provenance="local_estimate",
                cost_usd=None,
                dimensions={},
                extra={},
            )
        )
        if (index + 1) % batch_size == 0:
            await session.flush()
    await session.flush()
    return count
