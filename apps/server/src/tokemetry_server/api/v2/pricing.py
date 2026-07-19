"""v2 pricing administration: reprice and revert (task 64.6).

Both operations require the ``admin:pricing`` scope. Reprice recomputes cost for
a time range under a new pricing version, retaining prior rows; revert restores
a named prior version. Each writes an audit entry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import (
    RepriceRequest,
    RepriceResponse,
    RevertRequest,
)
from tokemetry_server.config import Settings
from tokemetry_server.scopes import ADMIN_PRICING
from tokemetry_server.services.repricing import reprice, revert

router = APIRouter(prefix="/api/v2/pricing", tags=["pricing"])


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
