"""Event revision engine: finality, sequence, conflicts, corrections.

Implements the PRD Section 12.4 state machine for one ``(provider, event_id)``
against the active row in ``usage_events_v2``. Every incoming v2 event resolves
to exactly one outcome:

- ``accepted``  -- a brand-new event id was inserted.
- ``updated``   -- a newer state superseded the prior one (a higher-sequence
  snapshot, or a final over a snapshot); the prior state is archived to
  ``usage_event_revisions`` with reason ``superseded`` (FR-IDEMP-002/003/004).
- ``duplicate`` -- a no-op: a byte-identical replay (FR-IDEMP-007), or a stale
  or out-of-order event (a later snapshot after a final, FR-EVENT-008, or an
  older snapshot) that is correctly ignored.
- ``rejected``  -- a genuine conflict that is refused and surfaced as a
  ``sequence_conflict`` data-quality event: two states at the same sequence with
  differing payloads (FR-IDEMP-008), or a final over a final without the
  correction flag (FR-IDEMP-005).
- ``corrected`` -- an authorized final-over-final correction (D-002,
  FR-EVENT-026): the prior final is archived with reason ``correction``, actor,
  timestamp, and reason text (FR-IDEMP-006).

The state machine lives in the pure :func:`resolve`; :class:`RevisionEngine`
reads the active row, calls it, and applies the decision within the caller's
transaction. ``ConflictMode.KEEP_MAX`` reproduces the legacy v1 keep-maximum
behavior exactly (FR-IDEMP-012) so v1 traffic mapped into the v2 ledger stays
wire-compatible; the mode is explicit and tested rather than accidental.

The ``admin:corrections`` scope required for a correction is enforced upstream
at the API boundary (task 62.6); :func:`resolve` receives an already-validated
``correction`` flag.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import UsageEventV2

from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService


class Outcome(enum.StrEnum):
    """The terminal classification of one processed event (FR-IDEMP-011)."""

    ACCEPTED = "accepted"
    UPDATED = "updated"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class ConflictMode(enum.StrEnum):
    """How same-key conflicts are resolved.

    REVISION: the full v2 finality/sequence state machine.
    KEEP_MAX: legacy v1 compatibility -- keep the row with the most output
        tokens (FR-IDEMP-012), archiving nothing.
    """

    REVISION = "revision"
    KEEP_MAX = "keep_max"


class RevisionReason(enum.StrEnum):
    """Why a prior state was archived to ``usage_event_revisions``."""

    SUPERSEDED = "superseded"
    CONFLICT = "conflict"
    CORRECTION = "correction"


@dataclass(frozen=True)
class EventState:
    """The fields of one event state the resolver needs to decide."""

    finality: str
    sequence: int
    output_tokens: int
    fingerprint: str


@dataclass(frozen=True)
class RevisionDecision:
    """What to do with an incoming event.

    ``write`` means persist the incoming event as the new active state.
    ``archive_reason`` (when set) means archive the *previous* active state with
    that reason before writing. ``conflict`` means record a data-quality
    ``sequence_conflict`` event.
    """

    outcome: Outcome
    write: bool
    archive_reason: RevisionReason | None = None
    conflict: bool = False


def resolve(
    incoming: EventState,
    existing: EventState | None,
    mode: ConflictMode = ConflictMode.REVISION,
    correction: bool = False,
) -> RevisionDecision:
    """Decide the outcome for ``incoming`` given the ``existing`` active state.

    Pure: no I/O, so the full ``(existing state x incoming)`` matrix is unit
    tested directly. ``correction`` must already be authorized by the caller.
    """
    if existing is None:
        return RevisionDecision(Outcome.ACCEPTED, write=True)

    if mode is ConflictMode.KEEP_MAX:
        return _resolve_keep_max(incoming, existing)

    # Identical replay in any state is a no-op (FR-IDEMP-007).
    if incoming.fingerprint == existing.fingerprint:
        return RevisionDecision(Outcome.DUPLICATE, write=False)

    if existing.finality == "final":
        return _resolve_over_final(incoming, correction=correction)

    return _resolve_over_snapshot(incoming, existing)


def _resolve_keep_max(incoming: EventState, existing: EventState) -> RevisionDecision:
    """Legacy keep-maximum-output resolution (FR-IDEMP-012)."""
    if incoming.fingerprint == existing.fingerprint:
        return RevisionDecision(Outcome.DUPLICATE, write=False)
    if incoming.output_tokens >= existing.output_tokens:
        return RevisionDecision(Outcome.UPDATED, write=True)
    return RevisionDecision(Outcome.DUPLICATE, write=False)


def _resolve_over_final(incoming: EventState, *, correction: bool) -> RevisionDecision:
    """Resolve an event arriving against an existing *final* state."""
    if incoming.finality == "final":
        if correction:
            return RevisionDecision(
                Outcome.CORRECTED, write=True, archive_reason=RevisionReason.CORRECTION
            )
        # Two disagreeing finals without an authorized correction (FR-IDEMP-005).
        return RevisionDecision(Outcome.REJECTED, write=False, conflict=True)
    # A later snapshot never supersedes a final (FR-EVENT-008): ignore it.
    return RevisionDecision(Outcome.DUPLICATE, write=False)


def _resolve_over_snapshot(
    incoming: EventState, existing: EventState
) -> RevisionDecision:
    """Resolve an event arriving against an existing *snapshot* state."""
    if incoming.finality == "final":
        # Final supersedes any snapshot (FR-IDEMP-004).
        return RevisionDecision(
            Outcome.UPDATED, write=True, archive_reason=RevisionReason.SUPERSEDED
        )
    if incoming.sequence > existing.sequence:
        # Higher-sequence snapshot supersedes a lower one (FR-IDEMP-003).
        return RevisionDecision(
            Outcome.UPDATED, write=True, archive_reason=RevisionReason.SUPERSEDED
        )
    if incoming.sequence == existing.sequence:
        # Same sequence, different payload -> conflict (FR-IDEMP-008).
        return RevisionDecision(Outcome.REJECTED, write=False, conflict=True)
    # An older snapshot arriving late: ignore it.
    return RevisionDecision(Outcome.DUPLICATE, write=False)


def _to_utc(value: datetime | None) -> datetime | None:
    """Normalize a timestamp to UTC so stored rows and fingerprints align."""
    if value is None:
        return None
    return value.astimezone(UTC)


def usage_event_v2_row(
    event: UsageEventV2, source_id: int | None = None, cost: Decimal | None = None
) -> dict[str, Any]:
    """Project a v2 wire event onto a ``usage_events_v2`` row dict.

    Shared by the revision engine, the v2 ingest service (task 62.5), and the
    v1-to-v2 mapper (task 62.9) so every path writes an identical row shape.
    ``source_id`` stays ``None`` until Task 63 resolves source identity. ``cost``
    fills the transitional ``cost_usd`` column (task 62.9): the v1 ingest path
    passes its keep-max cost; native v2 ingest leaves it ``None`` (priced later
    by the cost engine, Task 64).
    """
    return {
        "provider": event.provider,
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "event_kind": str(event.event_kind),
        "finality": str(event.finality),
        "sequence": event.sequence,
        "logical_request_id": event.logical_request_id,
        "attempt_id": event.attempt_id,
        "provider_request_id": event.provider_request_id,
        "provider_response_id": event.provider_response_id,
        "requested_model": event.requested_model,
        "routed_model": event.routed_model,
        "native_model": event.native_model,
        "ts_started": _to_utc(event.ts_started),
        "ts_first_token": _to_utc(event.ts_first_token),
        "ts_completed": _to_utc(event.ts_completed),
        "machine": event.machine,
        "project": event.project,
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "environment": event.environment,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "cache_read_tokens": event.cache_read_tokens,
        "cache_write_short_tokens": event.cache_write_short_tokens,
        "cache_write_long_tokens": event.cache_write_long_tokens,
        "reasoning_tokens": event.reasoning_tokens,
        "success": event.success,
        "outcome": event.outcome,
        "http_status": event.http_status,
        "stop_reason": event.stop_reason,
        "service_tier": event.service_tier,
        "streaming": event.streaming,
        "latency_ms": event.latency_ms,
        "time_to_first_token_ms": event.time_to_first_token_ms,
        "tool_call_count": event.tool_call_count,
        "tool_histogram": event.tool_histogram,
        "provenance": str(event.provenance),
        "cost_usd": cost,
        "source_id": source_id,
        "routing": event.routing.model_dump(mode="json") if event.routing else None,
        "dimensions": dict(event.dimensions),
        "extra": dict(event.extra),
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
    }


def _fingerprint_default(value: Any) -> str:
    """JSON fallback: render datetimes as UTC ISO strings, others as ``str``."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat()
    return str(value)


def row_fingerprint(row: dict[str, Any]) -> str:
    """A canonical, engine-independent identity string for a ledger row.

    Two rows fingerprint equal iff they represent the same stored state.
    Timestamps are normalized to UTC ISO so a tz-aware incoming row and the
    naive row SQLite reads back compare equal; keys are sorted so ordering
    never matters.
    """
    return json.dumps(row, sort_keys=True, default=_fingerprint_default)


def _row_from_orm(obj: models.UsageEventV2) -> dict[str, Any]:
    """Read every mapped column of an active row into a plain dict."""
    return {
        column.name: getattr(obj, column.name)
        for column in models.UsageEventV2.__table__.columns
    }


def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    """Render a row dict JSON-safe for the revision payload (datetimes to ISO)."""
    return {
        key: _fingerprint_default(value) if isinstance(value, datetime) else value
        for key, value in row.items()
    }


class RevisionEngine:
    """Applies :func:`resolve` to the ``usage_events_v2`` ledger."""

    def __init__(
        self, session: AsyncSession, data_quality: DataQualityService | None = None
    ) -> None:
        """Create the engine.

        Args:
            session: Active async session; the caller owns the transaction.
            data_quality: Optional sink for ``sequence_conflict`` records; when
                absent, conflicts are still rejected but not recorded.
        """
        self._session = session
        self._dq = data_quality

    async def apply(
        self,
        event: UsageEventV2,
        *,
        mode: ConflictMode = ConflictMode.REVISION,
        correction: bool = False,
        actor: str | None = None,
        reason_text: str | None = None,
        source_id: int | None = None,
        cost: Decimal | None = None,
    ) -> Outcome:
        """Resolve and persist one event; return its outcome.

        Reads the active row, decides via :func:`resolve`, archives the prior
        state when superseding or correcting, writes the new active state, and
        records a ``sequence_conflict`` data-quality event on a conflict.
        """
        row = usage_event_v2_row(event, source_id=source_id, cost=cost)
        existing_obj = await self._session.get(
            models.UsageEventV2, (event.provider, event.event_id)
        )

        existing_state: EventState | None = None
        if existing_obj is not None:
            existing_row = _row_from_orm(existing_obj)
            existing_state = EventState(
                finality=existing_obj.finality,
                sequence=existing_obj.sequence,
                output_tokens=existing_obj.output_tokens,
                fingerprint=row_fingerprint(existing_row),
            )

        incoming_state = EventState(
            finality=str(event.finality),
            sequence=event.sequence,
            output_tokens=event.output_tokens,
            fingerprint=row_fingerprint(row),
        )

        decision = resolve(incoming_state, existing_state, mode, correction)

        if decision.archive_reason is not None and existing_obj is not None:
            self._archive(existing_obj, decision.archive_reason, actor, reason_text)

        if decision.write:
            if existing_obj is None:
                self._session.add(models.UsageEventV2(**row))
            else:
                for key, value in row.items():
                    setattr(existing_obj, key, value)

        if decision.conflict and self._dq is not None:
            await self._dq.record_safe(
                "sequence_conflict",
                f"{event.provider}/{event.event_id}",
                event.ts_started,
                detail={
                    "incoming_finality": incoming_state.finality,
                    "incoming_sequence": incoming_state.sequence,
                    "existing_finality": existing_state.finality if existing_state else None,
                    "existing_sequence": existing_state.sequence if existing_state else None,
                },
            )

        return decision.outcome

    def _archive(
        self,
        existing_obj: models.UsageEventV2,
        reason: RevisionReason,
        actor: str | None,
        reason_text: str | None,
    ) -> None:
        """Write the prior active state to ``usage_event_revisions``.

        The payload envelopes the superseded row under ``previous`` and carries
        the human ``reason_text`` (only meaningful for corrections), so the full
        audit trail of an event id -- who, when, why, and the previous values --
        is reconstructable (FR-IDEMP-006).
        """
        note = reason_text if reason is RevisionReason.CORRECTION else None
        self._session.add(
            models.UsageEventRevision(
                provider=existing_obj.provider,
                event_id=existing_obj.event_id,
                sequence=existing_obj.sequence,
                finality=existing_obj.finality,
                payload={
                    "previous": _jsonable_row(_row_from_orm(existing_obj)),
                    "reason_text": note,
                },
                reason=str(reason),
                actor=actor,
                ts=datetime.now(UTC),
            )
        )
