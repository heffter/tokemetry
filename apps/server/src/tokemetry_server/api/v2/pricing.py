"""v2 pricing administration: reprice, revert, and rate-card import (tasks 64.6/64.9).

All operations require the ``admin:pricing`` scope. Reprice recomputes cost for
a time range under a new pricing version, retaining prior rows; revert restores
a named prior version; import diffs a LiteLLM + curated price set and applies it
as an audited, reviewable change. Each writes an audit entry.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import (
    ImportChangeOut,
    ImportRequest,
    ImportResponse,
    RepriceRequest,
    RepriceResponse,
    RevertRequest,
)
from tokemetry_server.config import Settings
from tokemetry_server.scopes import ADMIN_PRICING
from tokemetry_server.services.litellm_sync import (
    fetch_litellm_prices,
    import_rate_cards_from_data,
)
from tokemetry_server.services.pricing_import import (
    DigestMismatchError,
    RateCardChange,
    apply_import,
    compute_import_diff,
)
from tokemetry_server.services.repricing import reprice, revert

router = APIRouter(prefix="/api/v2/pricing", tags=["pricing"])

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
