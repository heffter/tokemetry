"""Source registry: auto-register reporting sources from v2 payloads.

A source is a gateway, collector, SDK, importer, or manual actor -- distinct
from a machine (FR-SOURCE-003), so one machine may host several
(FR-SOURCE-009). Every v2 ingest resolves each event's ``source`` object to a
``sources`` row, creating it on first sight (D-011) and advancing ``last_seen``
and ``version`` thereafter. Identity is ``(type, name, instance_id)``; a
``NULL`` instance id is matched explicitly so a source without one is not
duplicated. Labels are mutable without changing event identity (FR-SOURCE-010),
and revoking a source never deletes history (FR-SOURCE-012).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef

from tokemetry_server.db import models


class SourceRegistryService:
    """Resolves ``SourceRef`` payloads to ``sources`` rows, creating on first sight."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the service bound to the caller's transaction."""
        self._session = session

    async def resolve_or_create(
        self,
        source: SourceRef,
        ts: datetime,
        machine: str | None = None,
        token_label: str | None = None,
    ) -> int:
        """Return the id of the source, creating or refreshing its row.

        Idempotent on ``(type, name, instance_id)``: a first sighting inserts a
        row (``billing_mode='api_billed'`` by default, D-007); a repeat advances
        ``last_seen`` (never backwards), bumps ``version``, and fills a missing
        machine or token label without overwriting an existing one.
        """
        source_type = str(source.type)
        stmt = select(models.Source).where(
            models.Source.type == source_type,
            models.Source.name == source.name,
        )
        stmt = (
            stmt.where(models.Source.instance_id == source.instance_id)
            if source.instance_id is not None
            else stmt.where(models.Source.instance_id.is_(None))
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()

        if existing is None:
            row = models.Source(
                type=source_type,
                name=source.name,
                version=source.version,
                instance_id=source.instance_id,
                machine=machine,
                token_label=token_label,
                billing_mode="api_billed",
                first_seen=ts,
                last_seen=ts,
                revoked=False,
            )
            self._session.add(row)
            await self._session.flush()
            return row.id

        if ts > _as_naive(existing.last_seen, ts):
            existing.last_seen = ts
        existing.version = source.version
        if machine is not None and existing.machine is None:
            existing.machine = machine
        if token_label is not None and existing.token_label is None:
            existing.token_label = token_label
        return existing.id


def _as_naive(stored: datetime, incoming: datetime) -> datetime:
    """Align a stored timestamp's tz-awareness with ``incoming`` for comparison.

    SQLite reads timestamps back naive while ``incoming`` is tz-aware; compare on
    the same footing so ``last_seen`` advances correctly on both engines.
    """
    if stored.tzinfo is None and incoming.tzinfo is not None:
        return stored.replace(tzinfo=incoming.tzinfo)
    if stored.tzinfo is not None and incoming.tzinfo is None:
        return stored.replace(tzinfo=None)
    return stored
