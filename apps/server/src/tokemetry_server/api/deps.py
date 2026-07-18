"""Shared FastAPI dependencies: database session and ingest service.

Engine, session factory, and dialect name live on ``app.state`` (set in the
application lifespan) so dependencies are cheap request-scoped accessors.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.services.ingest import IngestService
from tokemetry_server.services.registries import (
    ModelRegistryService,
    ProviderRegistryService,
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session, committing on success."""
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_ingest_service(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> IngestService:
    """Provide an :class:`IngestService` bound to the request session."""
    settings = request.app.state.settings
    return IngestService(
        session=session,
        dialect_name=request.app.state.dialect_name,
        cost_fn=request.app.state.cost_fn,
        roots=settings.project_root_markers,
        providers=ProviderRegistryService(session),
        models_registry=ModelRegistryService(session),
        unknown_provider_policy=settings.registry_unknown_provider_policy,
    )
