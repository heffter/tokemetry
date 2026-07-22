"""v2 ingest endpoints: batch events, pre-flight validation, and readiness.

``POST /api/v2/ingest/events`` accepts a gzip-capable batch envelope, validates
every event against the v2 wire model and the privacy policy, and persists it
through :class:`IngestV2Service` in one transaction. ``POST
/api/v2/ingest/validate`` runs the same schema and privacy checks and returns
the structured error list without persisting anything (FR-INGEST-007), so
exporters can pre-flight a batch. ``GET /api/v2/ready`` is an unauthenticated
readiness probe reporting database and migration status without secrets
(FR-INGEST-018/019).

Validation failures on ``/events`` return HTTP 422 whose ``detail`` is
``{"errors": [...], "request_id": ...}`` -- the stable structured shape
(FR-INGEST-006) the generated clients consume; ``/validate`` reports the same
items in a 200 body. Ingest traffic is rate limited in its own class, separate
from query traffic (FR-INGEST-015).
"""

from __future__ import annotations

import gzip
import json
import math
from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import (
    AggregateImportV2,
    LimitSnapshotV2,
    UsageEventV2,
    usage_event_json_schema,
)

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import (
    IngestEventsResponse,
    MetaIngestResponse,
    ValidateResponse,
    ValidationErrorItem,
)
from tokemetry_server.config import Settings
from tokemetry_server.db.migrate import head_revision
from tokemetry_server.scopes import (
    ADMIN_CORRECTIONS,
    INGEST_AGGREGATES,
    INGEST_EVENTS,
    INGEST_LIMITS,
    QUERY_READ,
)
from tokemetry_server.services.data_quality import DataQualityService
from tokemetry_server.services.ingest_v2 import BatchValidationError, IngestV2Service
from tokemetry_server.services.ingest_v2_meta import MetaIngestV2Service
from tokemetry_server.services.privacy import PrivacyPolicy, PrivacyValidator

router = APIRouter(prefix="/api/v2", tags=["ingest"])


class _BatchEnvelope(BaseModel):
    """The outer shape of a v2 ingest batch.

    Events are kept as raw dicts so each is validated individually with a batch
    index (FR-INGEST-006) rather than collapsed into one envelope error.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=2, le=2)
    events: list[dict[str, Any]] = Field(min_length=1)
    return_ids: bool = False
    correction: bool = False


def _privacy_validator(settings: Settings) -> PrivacyValidator:
    """Build a privacy validator from settings (D-004/D-005)."""
    return PrivacyValidator(
        PrivacyPolicy(
            mode=settings.privacy_mode,
            dimension_allowlist=settings.privacy_dimension_allowlist_set,
            tool_names_enabled=settings.privacy_tool_names_enabled,
            max_event_bytes=settings.privacy_max_event_bytes,
            max_json_depth=settings.privacy_max_json_depth,
        )
    )


async def _load_body(request: Request, max_bytes: int) -> dict[str, Any]:
    """Read, gunzip if needed, size-check, and parse the JSON request body."""
    raw = await request.body()
    if "gzip" in request.headers.get("Content-Encoding", "").lower():
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "invalid gzip body"
            ) from exc
    if len(raw) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"batch body exceeds the {max_bytes} byte limit",
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid JSON body") from exc
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body must be a JSON object")
    return data


def _pydantic_items(error: ValidationError, index: int) -> list[ValidationErrorItem]:
    """Map a pydantic error to structured items at batch position ``index``."""
    return [
        ValidationErrorItem(
            index=index,
            field_path=".".join(str(part) for part in err["loc"]),
            code=str(err["type"]),
            message=str(err["msg"]),
        )
        for err in error.errors()
    ]


def _validate_envelope(
    data: dict[str, Any],
) -> tuple[_BatchEnvelope | None, list[ValidationErrorItem]]:
    """Validate the batch envelope; return it, or None plus envelope errors."""
    try:
        return _BatchEnvelope.model_validate(data), []
    except ValidationError as exc:
        return None, _pydantic_items(exc, -1)


def _validate_events(
    envelope: _BatchEnvelope,
) -> tuple[list[tuple[int, UsageEventV2]], list[ValidationErrorItem]]:
    """Validate each raw event; return (index, event) pairs plus schema errors."""
    valid: list[tuple[int, UsageEventV2]] = []
    errors: list[ValidationErrorItem] = []
    for index, raw in enumerate(envelope.events):
        try:
            valid.append((index, UsageEventV2.model_validate(raw)))
        except ValidationError as exc:
            errors.extend(_pydantic_items(exc, index))
    return valid, errors


def _raise_validation(errors: list[ValidationErrorItem], request_id: str | None) -> NoReturn:
    """Raise a 422 carrying the structured error list (FR-INGEST-006)."""
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        {"errors": [item.model_dump() for item in errors], "request_id": request_id},
    )


def _check_event_count(envelope: _BatchEnvelope, max_events: int) -> None:
    """Enforce the maximum batch event count (FR-INGEST-005)."""
    if len(envelope.events) > max_events:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"batch has {len(envelope.events)} events, over the {max_events} limit",
        )


def ingest_rate_guard(
    scope: str,
) -> Callable[[Request, Principal], Awaitable[Principal]]:
    """Build a dependency requiring ``scope`` and charging the ingest bucket."""

    async def _guard(
        request: Request, principal: Principal = Depends(require_scopes(scope))
    ) -> Principal:
        limiter = request.app.state.ingest_rate_limiter
        retry_after = limiter.check(principal.label)
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "ingest rate limit exceeded",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
        return principal

    return _guard


def _check_source_allowlist(
    principal: Principal,
    events: list[tuple[int, UsageEventV2]],
    request_id: str | None,
) -> None:
    """Reject the batch if any event's source is outside the token allowlist.

    Optional per-token source allowlist (FR-INGEST-020, FR-SEC-004); when set, a
    batch reporting for a source name not on the list is refused with 403 and
    the same structured error shape as validation.
    """
    if principal.source_allowlist is None:
        return
    allowed = set(principal.source_allowlist)
    denied = [
        ValidationErrorItem(
            index=index,
            field_path="source.name",
            code="source_not_allowed",
            message=f"source {event.source.name!r} is not in the token allowlist",
        )
        for index, event in events
        if event.source.name not in allowed
    ]
    if denied:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"errors": [item.model_dump() for item in denied], "request_id": request_id},
        )


async def _publish(request: Request, message: dict[str, object]) -> None:
    """Publish a live event to the WebSocket broadcaster if present."""
    broadcaster = getattr(request.app.state, "broadcaster", None)
    if broadcaster is not None:
        await broadcaster.publish(message)


@router.post("/ingest/events", response_model=IngestEventsResponse)
async def ingest_events(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(ingest_rate_guard(INGEST_EVENTS)),
) -> IngestEventsResponse:
    """Validate and persist a batch of v2 usage events."""
    settings: Settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", None)
    data = await _load_body(request, settings.ingest_max_bytes)

    envelope, envelope_errors = _validate_envelope(data)
    if envelope is None:
        _raise_validation(envelope_errors, request_id)
    _check_event_count(envelope, settings.ingest_max_events)

    if envelope.correction and not principal.has_scope(ADMIN_CORRECTIONS):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "corrections require the admin:corrections scope",
        )

    valid, errors = _validate_events(envelope)
    if errors:
        _raise_validation(errors, request_id)
    _check_source_allowlist(principal, valid, request_id)
    events = [event for _, event in valid]

    service = IngestV2Service(
        session,
        privacy=_privacy_validator(settings),
        data_quality=DataQualityService(
            session, settings.data_quality_dedup_window_seconds
        ),
    )
    try:
        result = await service.ingest(
            events,
            token_label=principal.label,
            request_id=request_id,
            correction=envelope.correction,
            actor=principal.label,
            return_ids=envelope.return_ids,
        )
    except BatchValidationError as exc:
        _raise_validation(
            [ValidationErrorItem(**vars(issue)) for issue in exc.issues], request_id
        )

    await session.commit()
    try:
        await _publish(
            request,
            {
                "type": "events_v2",
                "accepted": result.accepted,
                "updated": result.updated,
            },
        )
    except Exception as exc:
        # Publish is best-effort: a failure must never fail committed ingest.
        logger.warning("v2 ingest websocket publish failed: {}", exc)

    return IngestEventsResponse(
        batch_id=result.batch_id,
        request_id=request_id,
        accepted=result.accepted,
        updated=result.updated,
        duplicate=result.duplicate,
        rejected=result.rejected,
        corrected=result.corrected,
        accepted_ids=result.accepted_ids if envelope.return_ids else None,
        updated_ids=result.updated_ids if envelope.return_ids else None,
        ids_truncated=result.ids_truncated,
    )


@router.post("/ingest/validate", response_model=ValidateResponse)
async def validate_events(
    request: Request,
    _: Principal = Depends(ingest_rate_guard(INGEST_EVENTS)),
) -> ValidateResponse:
    """Run schema and privacy checks without persisting anything."""
    settings: Settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", None)
    data = await _load_body(request, settings.ingest_max_bytes)

    envelope, errors = _validate_envelope(data)
    if envelope is None:
        return ValidateResponse(valid=False, request_id=request_id, errors=errors)
    _check_event_count(envelope, settings.ingest_max_events)

    valid, schema_errors = _validate_events(envelope)
    errors.extend(schema_errors)

    validator = _privacy_validator(settings)
    for index, event in valid:
        errors.extend(
            ValidationErrorItem(
                index=index,
                field_path=issue.field_path,
                code=issue.code,
                message=issue.message,
            )
            for issue in validator.issues(event)
        )

    return ValidateResponse(valid=not errors, request_id=request_id, errors=errors)


@router.get("/ready", tags=["meta"])
async def readiness(request: Request) -> JSONResponse:
    """Unauthenticated readiness probe: database and migration status.

    Returns ``503`` when the database is unreachable *or* its schema is not at
    the Alembic head this code expects. Reporting the stamped revision alone is
    misleading for deployments that do not auto-migrate
    (``TOKEMETRY_AUTO_MIGRATE=false``): a probe that stays green on a
    behind-head schema hides the very drift an operator needs to see, so the
    body carries ``migration_head`` and ``at_head`` and the status reflects them.
    """
    factory = request.app.state.session_factory
    database_ok = True
    revision: str | None = None
    try:
        async with factory() as session:
            await session.execute(sa.text("SELECT 1"))
            row = (
                await session.execute(sa.text("SELECT version_num FROM alembic_version"))
            ).first()
            revision = row[0] if row is not None else None
    except Exception:
        # Readiness reports failure in its body; it must never raise.
        database_ok = False

    head = head_revision()
    at_head = database_ok and revision is not None and revision == head
    if not database_ok:
        state = "unavailable"
    elif not at_head:
        state = "migrations_pending"
    else:
        state = "ready"

    payload = {
        "status": state,
        "database": "ok" if database_ok else "error",
        "migration": revision,
        "migration_head": head,
        "at_head": at_head,
    }
    code = status.HTTP_200_OK if at_head else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(payload, status_code=code)


@router.get("/schemas/usage-event", tags=["meta"])
async def usage_event_schema(
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> dict[str, Any]:
    """Serve the published JSON schema for the v2 usage event (FR-INGEST-012).

    Generated from the same ``UsageEventV2`` model the ingest endpoint
    validates against, so the served schema can never drift from enforcement.
    """
    return usage_event_json_schema()


def _validate_batch(
    data: dict[str, Any],
    list_key: str,
    model: type[LimitSnapshotV2] | type[AggregateImportV2],
    max_events: int,
) -> tuple[list[Any], list[ValidationErrorItem]]:
    """Validate a ``{schema_version, <list_key>: [...]}`` batch of ``model``."""
    errors: list[ValidationErrorItem] = []
    if data.get("schema_version") != 2:
        errors.append(
            ValidationErrorItem(
                index=-1,
                field_path="schema_version",
                code="invalid_schema_version",
                message="schema_version must be 2",
            )
        )
    raw_items = data.get(list_key)
    if not isinstance(raw_items, list) or not raw_items:
        errors.append(
            ValidationErrorItem(
                index=-1,
                field_path=list_key,
                code="invalid",
                message=f"{list_key} must be a non-empty list",
            )
        )
        return [], errors
    if len(raw_items) > max_events:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"batch has {len(raw_items)} items, over the {max_events} limit",
        )
    items: list[Any] = []
    for index, raw in enumerate(raw_items):
        try:
            items.append(model.model_validate(raw))
        except ValidationError as exc:
            errors.extend(_pydantic_items(exc, index))
    return items, errors


@router.post("/ingest/limits", response_model=MetaIngestResponse)
async def ingest_limits(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(ingest_rate_guard(INGEST_LIMITS)),
) -> MetaIngestResponse:
    """Append a batch of provider-neutral limit snapshots (append-only)."""
    settings: Settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", None)
    data = await _load_body(request, settings.ingest_max_bytes)

    snapshots, errors = _validate_batch(
        data, "snapshots", LimitSnapshotV2, settings.ingest_max_events
    )
    if errors:
        _raise_validation(errors, request_id)

    service = MetaIngestV2Service(session, request.app.state.dialect_name)
    batch_id, accepted = await service.ingest_limits(
        snapshots,
        token_label=principal.label,
        request_id=request_id,
        min_interval_seconds=settings.limit_snapshot_min_interval_seconds,
    )
    await session.commit()
    return MetaIngestResponse(batch_id=batch_id, request_id=request_id, accepted=accepted)


@router.post("/ingest/aggregates", response_model=MetaIngestResponse)
async def ingest_aggregates(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(ingest_rate_guard(INGEST_AGGREGATES)),
) -> MetaIngestResponse:
    """Upsert a batch of historical daily aggregate imports."""
    settings: Settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", None)
    data = await _load_body(request, settings.ingest_max_bytes)

    aggregates, errors = _validate_batch(
        data, "aggregates", AggregateImportV2, settings.ingest_max_events
    )
    if errors:
        _raise_validation(errors, request_id)

    service = MetaIngestV2Service(session, request.app.state.dialect_name)
    batch_id, accepted = await service.ingest_aggregates(
        aggregates, token_label=principal.label, request_id=request_id
    )
    await session.commit()
    return MetaIngestResponse(batch_id=batch_id, request_id=request_id, accepted=accepted)
