"""Version 2 API (provider-neutral).

Aggregates the v2 routers behind a single router the application mounts. New
v2 feature routers are included here as they land.
"""

from __future__ import annotations

from fastapi import APIRouter

from tokemetry_server.api.v2.ingest import router as _ingest_router
from tokemetry_server.api.v2.registries import router as _registries_router
from tokemetry_server.api.v2.sources import router as _sources_router

router = APIRouter()
router.include_router(_registries_router)
router.include_router(_ingest_router)
router.include_router(_sources_router)
