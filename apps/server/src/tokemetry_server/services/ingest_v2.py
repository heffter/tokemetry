"""Transactional v2 batch ingest orchestration.

Takes a schema-valid batch of :class:`UsageEventV2` events and, inside the
caller's single transaction (FR-IDEMP-009, NFR-REL-001):

1. Runs the privacy validator (task 62.2) over every event. In ``reject`` mode
   any prohibited-content or bounds violation is a *structural* failure: the
   whole batch is refused with a :class:`BatchValidationError` carrying the
   per-event index, field path, code, and message (FR-INGEST-006) before
   anything is written. In ``strip`` mode the cleaned events are used.
2. Resolves each event through the revision engine (task 62.4), which decides
   accepted/updated/duplicate/rejected/corrected and archives superseded
   states. A per-event conflict (``rejected``) is recorded as a data-quality
   event and counted -- it does **not** fail the batch; only a structural
   failure or a database error rolls the batch back.
3. Writes one ``ingest_batches`` row with the server-generated batch id, source
   identity, token label, the five outcome counts (FR-IDEMP-011), and the
   request id (FR-INGEST-008/016).

The service is transport-agnostic and never commits -- the HTTP route (task
62.6) owns the transaction and performs the post-commit WebSocket publish so a
publish failure can never roll back accepted ingest (NFR-REL-008). No event
content or bearer token is logged (FR-INGEST-017); the service logs nothing.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import UsageEventV2

from tokemetry_server.db import models
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.logical_requests import LogicalRequestService
from tokemetry_server.services.privacy import PrivacyValidator
from tokemetry_server.services.revisions import ConflictMode, Outcome, RevisionEngine
from tokemetry_server.services.sources import SourceRegistryService

#: Default cap on the number of ids echoed back per list (FR-INGEST-009).
DEFAULT_MAX_RETURNED_IDS = 1000


@dataclass(frozen=True)
class BatchIssue:
    """One per-event validation failure with its batch position."""

    index: int
    field_path: str
    code: str
    message: str


class BatchValidationError(Exception):
    """A batch was refused before persistence; carries structured issues."""

    def __init__(self, issues: list[BatchIssue]) -> None:
        """Store ``issues`` for the route to render (FR-INGEST-006)."""
        self.issues = issues
        super().__init__(f"{len(issues)} validation issue(s)")


@dataclass(frozen=True)
class IngestV2Result:
    """The outcome of one ingested batch."""

    batch_id: str
    accepted: int = 0
    updated: int = 0
    duplicate: int = 0
    rejected: int = 0
    corrected: int = 0
    accepted_ids: list[str] = field(default_factory=list)
    updated_ids: list[str] = field(default_factory=list)
    ids_truncated: bool = False


class IngestV2Service:
    """Persists a v2 event batch idempotently and transactionally."""

    def __init__(
        self,
        session: AsyncSession,
        privacy: PrivacyValidator | None = None,
        data_quality: DataQualityService | None = None,
        max_returned_ids: int = DEFAULT_MAX_RETURNED_IDS,
    ) -> None:
        """Create the service.

        Args:
            session: Active async session; the caller owns the transaction.
            privacy: Privacy validator; a default-policy one when omitted.
            data_quality: Sink for ``sequence_conflict`` records; optional.
            max_returned_ids: Cap on each echoed id list (FR-INGEST-009).
        """
        self._session = session
        self._privacy = privacy or PrivacyValidator()
        self._engine = RevisionEngine(session, data_quality)
        self._logical_requests = LogicalRequestService(session)
        self._sources = SourceRegistryService(session)
        self._max_returned_ids = max_returned_ids

    async def ingest(
        self,
        events: list[UsageEventV2],
        *,
        token_label: str | None = None,
        request_id: str | None = None,
        mode: ConflictMode = ConflictMode.REVISION,
        correction: bool = False,
        actor: str | None = None,
        return_ids: bool = False,
    ) -> IngestV2Result:
        """Validate and persist ``events``; return per-outcome counts.

        Each event's ``source`` object is resolved to a ``sources`` row
        (auto-registered on first sight, task 63.1) and stamped onto the ledger
        row as ``source_id``.

        Raises:
            BatchValidationError: If any event fails privacy validation in
                ``reject`` mode; nothing is persisted.
        """
        cleaned = self._validate(events)

        counts: Counter[Outcome] = Counter()
        accepted_ids: list[str] = []
        updated_ids: list[str] = []
        source_cache: dict[tuple[str, str, str | None], int] = {}
        batch_source_id: int | None = None
        for event in cleaned:
            source_id = await self._resolve_source(event, token_label, source_cache)
            if batch_source_id is None:
                batch_source_id = source_id
            outcome = await self._engine.apply(
                event,
                mode=mode,
                correction=correction,
                actor=actor,
                source_id=source_id,
            )
            counts[outcome] += 1
            if return_ids and outcome is Outcome.ACCEPTED:
                accepted_ids.append(event.event_id)
            elif return_ids and outcome is Outcome.UPDATED:
                updated_ids.append(event.event_id)

        # Recompute each touched logical request from the now-current ledger
        # rows (order-independent, correction-safe; task 62.11).
        touched = {
            (event.provider, event.logical_request_id)
            for event in cleaned
            if event.logical_request_id is not None
        }
        for provider, logical_request_id in touched:
            await self._logical_requests.recompute(provider, logical_request_id)

        batch_id = uuid.uuid4().hex
        capped_accepted, truncated_a = self._cap(accepted_ids)
        capped_updated, truncated_u = self._cap(updated_ids)

        self._session.add(
            models.IngestBatch(
                batch_id=batch_id,
                source_id=batch_source_id,
                token_label=token_label,
                accepted=counts[Outcome.ACCEPTED],
                updated=counts[Outcome.UPDATED],
                duplicate=counts[Outcome.DUPLICATE],
                rejected=counts[Outcome.REJECTED],
                corrected=counts[Outcome.CORRECTED],
                schema_version=2,
                received_at=datetime.now(UTC),
                request_id=request_id,
            )
        )

        return IngestV2Result(
            batch_id=batch_id,
            accepted=counts[Outcome.ACCEPTED],
            updated=counts[Outcome.UPDATED],
            duplicate=counts[Outcome.DUPLICATE],
            rejected=counts[Outcome.REJECTED],
            corrected=counts[Outcome.CORRECTED],
            accepted_ids=capped_accepted,
            updated_ids=capped_updated,
            ids_truncated=truncated_a or truncated_u,
        )

    async def _resolve_source(
        self,
        event: UsageEventV2,
        token_label: str | None,
        cache: dict[tuple[str, str, str | None], int],
    ) -> int:
        """Resolve an event's source to a source id, caching per distinct source."""
        key = (str(event.source.type), event.source.name, event.source.instance_id)
        source_id = cache.get(key)
        if source_id is None:
            source_id = await self._sources.resolve_or_create(
                event.source,
                event.ts_started,
                machine=event.machine,
                token_label=token_label,
            )
            cache[key] = source_id
        return source_id

    def _validate(self, events: list[UsageEventV2]) -> list[UsageEventV2]:
        """Run privacy validation; raise on structural failure, else clean."""
        issues: list[BatchIssue] = []
        cleaned: list[UsageEventV2] = []
        for index, event in enumerate(events):
            result = self._privacy.sanitize(event)
            issues.extend(
                BatchIssue(index, issue.field_path, issue.code, issue.message)
                for issue in result.issues
            )
            cleaned.append(result.event)
        if issues:
            raise BatchValidationError(issues)
        return cleaned

    def _cap(self, ids: list[str]) -> tuple[list[str], bool]:
        """Cap an id list to the response-size limit (FR-INGEST-009)."""
        if len(ids) <= self._max_returned_ids:
            return ids, False
        return ids[: self._max_returned_ids], True
