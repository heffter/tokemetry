"""v2 ingest for limit snapshots and historical aggregate imports.

The two non-event v2 ingest paths, kept apart from the attempt-event pipeline
(:mod:`ingest_v2`) because their persistence differs:

- **Limit snapshots** are append-only (FR-LIMIT parity with v1): each snapshot
  is a new ``limit_snapshots`` row, so replaying a batch appends again. The v2
  extended dimensions (``account``, ``organization``, ``source``, ``remaining``,
  ``limit_amount``, ``unit``) have no columns yet -- they are added in Task 69 --
  so they are preserved in the row's ``raw`` JSON under a ``v2_dimensions`` key
  until that migration promotes them to columns.
- **Aggregate imports** upsert ``daily_rollups`` on its grain exactly like the
  v1 bootstrap path, so re-importing the same day converges (replace, not
  accumulate). ``reasoning_tokens`` folds into ``total_tokens`` until the rollup
  grain gains a reasoning column (Task 66).

Both write an ``ingest_batches`` row (server batch id, token label, request id)
for traceability, and both run inside the caller's single transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import AggregateImportV2, LimitSnapshotV2

from tokemetry_server.db import models
from tokemetry_server.db.upsert import daily_rollups_upsert


class MetaIngestV2Service:
    """Persists v2 limit snapshots and aggregate imports transactionally."""

    def __init__(self, session: AsyncSession, dialect_name: str) -> None:
        """Create the service.

        Args:
            session: Active async session; the caller owns the transaction.
            dialect_name: ``"postgresql"`` or ``"sqlite"`` for the upsert.
        """
        self._session = session
        self._dialect = dialect_name

    async def ingest_limits(
        self,
        snapshots: list[LimitSnapshotV2],
        *,
        token_label: str | None = None,
        request_id: str | None = None,
        source_id: int | None = None,
    ) -> tuple[str, int]:
        """Append limit snapshots; return the batch id and accepted count.

        The v2 dimensions now persist to dedicated columns (Task 69.2) rather
        than the ``raw`` stash; ``source_id`` is the reporting source, and the
        per-snapshot ``source`` reference (if any) is kept in ``raw``.
        """
        self._session.add_all(
            models.LimitSnapshot(
                provider=snapshot.provider,
                machine=snapshot.machine,
                ts=snapshot.ts,
                window_kind=snapshot.window_kind,
                utilization_pct=snapshot.utilization_pct,
                resets_at=snapshot.resets_at,
                provenance=str(snapshot.provenance),
                account=snapshot.account,
                organization=snapshot.organization,
                source_id=source_id,
                limit_amount=snapshot.limit_amount,
                remaining=snapshot.remaining,
                unit=snapshot.unit,
                raw=(
                    {"source": snapshot.source.model_dump(mode="json")}
                    if snapshot.source
                    else {}
                ),
            )
            for snapshot in snapshots
        )
        batch_id = self._record_batch(
            len(snapshots), token_label=token_label, request_id=request_id, source_id=source_id
        )
        return batch_id, len(snapshots)

    async def ingest_aggregates(
        self,
        aggregates: list[AggregateImportV2],
        *,
        token_label: str | None = None,
        request_id: str | None = None,
        source_id: int | None = None,
    ) -> tuple[str, int]:
        """Upsert aggregate rollups; return the batch id and accepted count."""
        rows = _dedupe_rollup_rows([_rollup_row(aggregate) for aggregate in aggregates])
        if rows:
            stmt = daily_rollups_upsert(self._dialect, models.DailyRollup.__table__, rows)
            await self._session.execute(stmt)
        batch_id = self._record_batch(
            len(aggregates), token_label=token_label, request_id=request_id, source_id=source_id
        )
        return batch_id, len(aggregates)

    def _record_batch(
        self,
        accepted: int,
        *,
        token_label: str | None,
        request_id: str | None,
        source_id: int | None,
    ) -> str:
        """Write an ingest_batches row and return its server-generated id."""
        batch_id = uuid.uuid4().hex
        self._session.add(
            models.IngestBatch(
                batch_id=batch_id,
                source_id=source_id,
                token_label=token_label,
                accepted=accepted,
                updated=0,
                duplicate=0,
                rejected=0,
                corrected=0,
                schema_version=2,
                received_at=datetime.now(UTC),
                request_id=request_id,
            )
        )
        return batch_id


def _rollup_row(aggregate: AggregateImportV2) -> dict[str, Any]:
    """Project an aggregate import onto a ``daily_rollups`` row dict."""
    return {
        "day": aggregate.day,
        "provider": aggregate.provider,
        "machine": aggregate.machine or "",
        "model": aggregate.native_model,
        "project": "",
        "input_tokens": aggregate.input_tokens,
        "output_tokens": aggregate.output_tokens,
        "cache_read_tokens": aggregate.cache_read_tokens,
        "cache_write_short_tokens": aggregate.cache_write_short_tokens,
        "cache_write_long_tokens": aggregate.cache_write_long_tokens,
        "total_tokens": aggregate.total_tokens,
        "cost_usd": None,
        "provenance": str(aggregate.provenance),
    }


def _dedupe_rollup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows sharing the rollup grain, keeping the last (replace).

    The upsert rejects two rows targeting the same conflict key in one
    statement, so a batch that repeats a ``(day, provider, machine, model,
    project)`` grain keeps the last occurrence -- matching the replace-not-
    accumulate semantics of the rollup upsert.
    """
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row["day"], row["provider"], row["machine"], row["model"], row["project"])
        best[key] = row
    return list(best.values())
