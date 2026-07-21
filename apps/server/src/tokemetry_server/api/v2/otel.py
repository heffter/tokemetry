"""Feature-flagged OTLP/HTTP trace receiver (Task 71.3, FR-OTEL-004/007).

`POST /api/v2/otel/v1/traces` accepts an OTLP/HTTP JSON trace export,
converts the GenAI spans into v2 attempt events (content stripped), and ingests
them like any other batch. Authenticated with the same ingest tokens as the v2
ingest surface (`ingest:events`). The router is mounted only when
``otel_receiver_enabled`` is set, so the endpoint is absent by default (D-009);
core ingest never depends on it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.otel.convert import spans_to_events
from tokemetry_server.otel.receiver import OtlpParseError, parse_otlp_json
from tokemetry_server.scopes import INGEST_EVENTS
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.ingest_v2 import BatchValidationError, IngestV2Service

router = APIRouter(prefix="/api/v2/otel", tags=["otel"])


@router.post("/v1/traces")
async def receive_traces(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(INGEST_EVENTS)),
) -> dict[str, Any]:
    """Ingest the GenAI spans in an OTLP/HTTP JSON trace export."""
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected a JSON object")

    try:
        spans = parse_otlp_json(payload)
    except OtlpParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    events = spans_to_events(spans)
    service = IngestV2Service(session, data_quality=DataQualityService(session))
    try:
        result = await service.ingest(
            events, token_label=principal.label, request_id=request.state.request_id
        )
    except BatchValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"errors": [issue.__dict__ for issue in exc.issues]},
        ) from exc

    # OTLP success response shape; the accepted count is an extra field OTLP
    # clients ignore but is handy for debugging.
    return {"partialSuccess": {}, "accepted": result.accepted}
