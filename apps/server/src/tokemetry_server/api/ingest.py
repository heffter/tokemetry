"""Ingest routes: events, limits, and bootstrap aggregates.

All routes are idempotent and authenticated. A malformed batch is rejected
whole (all-or-nothing) with a 422; sanity-check failures surface as 400.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from tokemetry_server.api.auth import require_token
from tokemetry_server.api.deps import get_ingest_service
from tokemetry_server.api.schemas import (
    BootstrapIngest,
    EventsIngest,
    IngestResult,
    LimitsIngest,
)
from tokemetry_server.services.ingest import IngestService
from tokemetry_server.services.validation import ValidationError

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


@router.post("/events", response_model=IngestResult)
async def ingest_events(
    payload: EventsIngest,
    service: IngestService = Depends(get_ingest_service),
    _: str = Depends(require_token),
) -> IngestResult:
    """Ingest a batch of usage events (idempotent, keep-max dedup)."""
    events = [event.to_core(payload.machine.name) for event in payload.events]
    try:
        return await service.ingest_events(payload.machine, events)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/limits", response_model=IngestResult)
async def ingest_limits(
    payload: LimitsIngest,
    service: IngestService = Depends(get_ingest_service),
    _: str = Depends(require_token),
) -> IngestResult:
    """Ingest a batch of limit snapshots."""
    snapshots = [snapshot.to_core(payload.machine.name) for snapshot in payload.snapshots]
    try:
        return await service.ingest_limits(payload.machine, snapshots)
    except ValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/bootstrap", response_model=IngestResult)
async def ingest_bootstrap(
    payload: BootstrapIngest,
    service: IngestService = Depends(get_ingest_service),
    _: str = Depends(require_token),
) -> IngestResult:
    """Ingest a batch of bootstrap daily aggregates."""
    aggregates = [aggregate.to_core(payload.machine.name) for aggregate in payload.aggregates]
    return await service.ingest_bootstrap(payload.machine, aggregates)
