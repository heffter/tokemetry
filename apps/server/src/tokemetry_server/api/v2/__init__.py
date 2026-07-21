"""Version 2 API (provider-neutral).

Aggregates the v2 routers behind a single router the application mounts. New
v2 feature routers are included here as they land.
"""

from __future__ import annotations

from fastapi import APIRouter

from tokemetry_server.api.v2.admin_data import router as _admin_data_router
from tokemetry_server.api.v2.audit import router as _audit_router
from tokemetry_server.api.v2.costs import router as _costs_router
from tokemetry_server.api.v2.ingest import router as _ingest_router
from tokemetry_server.api.v2.pricing import router as _pricing_router
from tokemetry_server.api.v2.registries import router as _registries_router
from tokemetry_server.api.v2.requests import router as _requests_router
from tokemetry_server.api.v2.resources import router as _resources_router
from tokemetry_server.api.v2.retention import router as _retention_router
from tokemetry_server.api.v2.sessions import router as _sessions_router
from tokemetry_server.api.v2.sources import router as _sources_router
from tokemetry_server.api.v2.usage import router as _usage_router

router = APIRouter()
router.include_router(_registries_router)
router.include_router(_ingest_router)
router.include_router(_sources_router)
router.include_router(_pricing_router)
router.include_router(_usage_router)
router.include_router(_costs_router)
router.include_router(_requests_router)
router.include_router(_sessions_router)
router.include_router(_resources_router)
router.include_router(_retention_router)
router.include_router(_admin_data_router)
router.include_router(_audit_router)
