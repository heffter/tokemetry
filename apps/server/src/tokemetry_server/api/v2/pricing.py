"""v2 pricing administration: reprice, revert, and rate-card import (tasks 64.6/64.9).

All operations require the ``admin:pricing`` scope. Reprice recomputes cost for
a time range under a new pricing version, retaining prior rows; revert restores
a named prior version; import diffs a LiteLLM + curated price set and applies it
as an audited, reviewable change. Each writes an audit entry.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import (
    ImportChangeOut,
    ImportRequest,
    ImportResponse,
    RateCardCloseRequest,
    RateCardCloseResponse,
    RateCardCreateRequest,
    RateCardMutationResponse,
    RateCardOut,
    RepriceRequest,
    RepriceResponse,
    RevertRequest,
    UnknownModelReportRow,
    UnpricedReportRow,
)
from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.scopes import ADMIN_PRICING, QUERY_READ
from tokemetry_server.services.litellm_sync import (
    fetch_litellm_prices,
    import_rate_cards_from_data,
)
from tokemetry_server.services.pricing_admin import (
    close_rate_card,
    create_rate_card,
    list_rate_cards,
    unknown_models_report,
    unpriced_report,
)
from tokemetry_server.services.pricing_import import (
    DigestMismatchError,
    RateCardChange,
    apply_import,
    compute_import_diff,
)
from tokemetry_server.services.pricing_v2 import OverlapError, current_pricing_version
from tokemetry_server.services.repricing import reprice, revert

router = APIRouter(prefix="/api/v2/pricing", tags=["pricing"])


def _rate_card_out(card: models.RateCard) -> RateCardOut:
    """Project a rate-card ORM row onto its wire schema."""
    return RateCardOut(
        id=card.id,
        provider=card.provider,
        native_model=card.native_model,
        unit_type=card.unit_type,
        effective_from=card.effective_from,
        effective_to=card.effective_to,
        currency=card.currency,
        region=card.region,
        service_tier=card.service_tier,
        mode=card.mode,
        context_bracket=card.context_bracket,
        unit_price=card.unit_price,
        source=card.source,
        priority=card.priority,
        override=card.override,
    )

#: Source label recorded on import audit entries.
_IMPORT_SOURCE = "litellm+official"


def _change_out(change: RateCardChange) -> ImportChangeOut:
    """Project a diff change onto its wire schema."""
    return ImportChangeOut(
        action=change.action,
        provider=change.provider,
        native_model=change.native_model,
        unit_type=change.unit_type,
        priority=change.priority,
        new_price=change.new_price,
    )


def _import_response(
    dry_run: bool,
    digest: str,
    new: int,
    superseded: int,
    unchanged: int,
    conflicts: int,
    changes: tuple[RateCardChange, ...],
) -> ImportResponse:
    """Build the import wire response from a diff or apply result."""
    return ImportResponse(
        dry_run=dry_run,
        digest=digest,
        new=new,
        superseded=superseded,
        unchanged=unchanged,
        conflicts=conflicts,
        changes=[_change_out(change) for change in changes],
    )


@router.post("/reprice", response_model=RepriceResponse)
async def reprice_endpoint(
    payload: RepriceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_PRICING)),
) -> RepriceResponse:
    """Recompute cost for a range under a new pricing version (audited)."""
    settings: Settings = request.app.state.settings
    result = await reprice(
        session, principal.label, payload.start, payload.end,
        payload.provider, payload.native_model,
        billing_mode_overrides=settings.billing_mode_override_map,
    )
    return RepriceResponse(
        pricing_version=result.pricing_version, affected=result.affected
    )


@router.post("/revert", response_model=RepriceResponse)
async def revert_endpoint(
    payload: RevertRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_PRICING)),
) -> RepriceResponse:
    """Re-activate a named prior pricing version for a range (audited)."""
    result = await revert(
        session, principal.label, payload.pricing_version, payload.start, payload.end,
        payload.provider, payload.native_model,
    )
    return RepriceResponse(
        pricing_version=result.pricing_version, affected=result.affected
    )


@router.post("/import", response_model=ImportResponse)
async def import_endpoint(
    payload: ImportRequest,
    request: Request,
    dry_run: bool = True,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_PRICING)),
) -> ImportResponse:
    """Diff (``dry_run=true``) or apply a LiteLLM + curated rate-card import.

    A dry run returns the structured diff and a digest without persisting. To
    apply, call again with ``dry_run=false`` and the dry run's ``digest``; the
    apply is rejected (409) if the stored rates changed since the dry run.
    """
    now = datetime.now(UTC)
    effective_from = now.date()
    data = await fetch_litellm_prices(request.app.state.http_client)
    rows = import_rate_cards_from_data(data, effective_from, now)

    if dry_run:
        diff = await compute_import_diff(session, rows, effective_from)
        return _import_response(
            True, diff.digest, diff.new_count, diff.superseded_count,
            diff.unchanged_count, diff.conflict_count, diff.changes,
        )

    if payload.digest is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "digest is required to apply an import"
        )
    try:
        result = await apply_import(
            session, rows, effective_from, payload.digest,
            principal.label, _IMPORT_SOURCE, now,
        )
    except DigestMismatchError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _import_response(
        False, result.digest, result.applied_new, result.applied_superseded,
        result.unchanged, result.conflicts, result.changes,
    )


@router.get("", response_model=list[RateCardOut])
async def list_rate_cards_endpoint(
    provider: str | None = Query(default=None),
    native_model: str | None = Query(default=None),
    unit_type: str | None = Query(default=None),
    active_on: date | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> list[RateCardOut]:
    """List rate cards, filterable by grain and active-on date."""
    cards = await list_rate_cards(session, provider, native_model, unit_type, active_on)
    return [_rate_card_out(card) for card in cards]


@router.post("", response_model=RateCardMutationResponse, status_code=status.HTTP_201_CREATED)
async def create_rate_card_endpoint(
    payload: RateCardCreateRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_PRICING)),
) -> RateCardMutationResponse:
    """Create a rate card (manual price or override); rejects date overlaps."""
    try:
        card = await create_rate_card(
            session, principal.label, datetime.now(UTC),
            provider=payload.provider,
            native_model=payload.native_model,
            unit_type=payload.unit_type,
            effective_from=payload.effective_from,
            unit_price=payload.unit_price,
            currency=payload.currency,
            mode=payload.mode,
            service_tier=payload.service_tier,
            context_bracket=payload.context_bracket,
            region=payload.region,
            source=payload.source,
            priority=payload.priority,
            override=payload.override,
            effective_to=payload.effective_to,
        )
    except OverlapError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    version = await current_pricing_version(session)
    return RateCardMutationResponse(
        rate_card=_rate_card_out(card), pricing_version=version
    )


@router.post("/{rate_card_id}/close", response_model=RateCardCloseResponse)
async def close_rate_card_endpoint(
    rate_card_id: int,
    payload: RateCardCloseRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_PRICING)),
) -> RateCardCloseResponse:
    """Close a rate card by setting its effective_to date (audited)."""
    card = await close_rate_card(
        session, principal.label, rate_card_id, payload.effective_to, datetime.now(UTC)
    )
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown rate card")
    version = await current_pricing_version(session)
    return RateCardCloseResponse(rate_card_id=rate_card_id, pricing_version=version)


@router.get("/reports/unpriced", response_model=list[UnpricedReportRow])
async def unpriced_report_endpoint(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> list[UnpricedReportRow]:
    """Aggregate active events that are unpriced or partially priced (US-010)."""
    return [
        UnpricedReportRow(
            provider=row.provider,
            native_model=row.native_model,
            cost_status=row.cost_status,
            event_count=row.event_count,
        )
        for row in await unpriced_report(session)
    ]


@router.get("/reports/unknown-models", response_model=list[UnknownModelReportRow])
async def unknown_models_report_endpoint(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(QUERY_READ)),
) -> list[UnknownModelReportRow]:
    """List unknown-model observations recorded at ingest (US-010)."""
    return [
        UnknownModelReportRow(
            provider=row.provider,
            native_model=row.native_model,
            observations=row.observations,
            resolved=row.resolved,
            last_seen=row.last_seen,
        )
        for row in await unknown_models_report(session)
    ]
