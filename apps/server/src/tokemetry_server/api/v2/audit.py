"""v2 audit-log review (Task 70.4).

`GET /api/v2/admin/audit` returns the append-only administrative audit trail,
newest first, filterable by action and actor. Requires the `admin:retention`
scope (the security/operations review capability). The log has no delete path;
it ages out only under the retention policy's ``audit_records`` category.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import AuditEntryOut
from tokemetry_server.scopes import ADMIN_RETENTION
from tokemetry_server.services.audit import list_audit

router = APIRouter(prefix="/api/v2/admin/audit", tags=["audit"])


@router.get("", response_model=list[AuditEntryOut])
async def list_audit_endpoint(
    action: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(ADMIN_RETENTION)),
) -> list[AuditEntryOut]:
    """Return recent audit entries, newest first, with optional filters."""
    rows = await list_audit(session, action=action, actor=actor, limit=limit)
    return [
        AuditEntryOut(
            id=row.id,
            ts=row.ts,
            actor=row.actor,
            action=row.action,
            subject=row.subject,
            detail=row.detail,
            request_id=row.request_id,
        )
        for row in rows
    ]
