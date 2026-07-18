"""Registry read endpoints: providers and models.

``GET /api/v2/providers`` returns all provider registry metadata;
``GET /api/v2/models`` returns model registry rows filterable by provider and
lifecycle, each with its alias spellings. Both are read-only and authenticated
with the standard bearer token.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import require_token
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import ModelOut, ProviderOut
from tokemetry_server.db import models

router = APIRouter(prefix="/api/v2", tags=["registry"])

#: Model lifecycle values accepted as a filter (FR-MODEL-004).
Lifecycle = Literal["active", "deprecated", "retired", "unknown"]


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[ProviderOut]:
    """Return every registered and observed provider, ordered by id."""
    rows = (
        await session.execute(select(models.Provider).order_by(models.Provider.id))
    ).scalars().all()
    return [
        ProviderOut(
            id=row.id,
            display_name=row.display_name,
            aliases=list(row.aliases or []),
            pricing_strategy=row.pricing_strategy,
            limit_semantics=row.limit_semantics,
            supported_dimensions=list(row.supported_dimensions or []),
            registered=row.registered,
        )
        for row in rows
    ]


@router.get("/models", response_model=list[ModelOut])
async def list_models(
    provider: str | None = Query(default=None),
    lifecycle: Lifecycle | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[ModelOut]:
    """Return model registry rows, optionally filtered by provider/lifecycle."""
    stmt = select(models.Model)
    if provider is not None:
        stmt = stmt.where(models.Model.provider == provider)
    if lifecycle is not None:
        stmt = stmt.where(models.Model.lifecycle == lifecycle)
    stmt = stmt.order_by(models.Model.provider, models.Model.native_model_id)
    rows = (await session.execute(stmt)).scalars().all()

    aliases = await _aliases_by_model(session, provider)
    return [
        ModelOut(
            provider=row.provider,
            native_model_id=row.native_model_id,
            lifecycle=row.lifecycle,
            capabilities=dict(row.capabilities or {}),
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            aliases=aliases.get((row.provider, row.native_model_id), []),
        )
        for row in rows
    ]


async def _aliases_by_model(
    session: AsyncSession, provider: str | None
) -> dict[tuple[str, str], list[str]]:
    """Group alias spellings by ``(provider, native_model_id)``."""
    stmt = select(models.ModelAlias)
    if provider is not None:
        stmt = stmt.where(models.ModelAlias.provider == provider)
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in (await session.execute(stmt)).scalars():
        grouped.setdefault((row.provider, row.native_model_id), []).append(row.alias)
    for spellings in grouped.values():
        spellings.sort()
    return grouped
