"""v2 administrative data deletion: targeted, dry-run/confirm erasure (Task 70.3).

`POST /api/v2/admin/data` deletes usage data scoped by source, machine, project,
and/or time range (FR-PRIV-007). A dry run (`dry_run=true`, the default) returns
per-table counts and a digest without touching data; a confirm
(`dry_run=false`) must echo that digest, is blocked by a legal hold, cascades
through costs/units/revisions/events, optionally recomputes the affected days'
rollups, and is audited (FR-PRIV-009). Requires the `admin:retention` scope.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import DeletionRequest, DeletionResponse
from tokemetry_server.scopes import ADMIN_RETENTION
from tokemetry_server.services.admin_deletion import (
    DeletionCriteria,
    DeletionDigestMismatchError,
    EmptyCriteriaError,
    execute_deletion,
    preview_deletion,
)
from tokemetry_server.services.retention import resolve_retention_policy

router = APIRouter(prefix="/api/v2/admin/data", tags=["admin-data"])


def _criteria(payload: DeletionRequest) -> DeletionCriteria:
    c = payload.criteria
    return DeletionCriteria(
        source=c.source,
        machine=c.machine,
        project=c.project,
        start=c.start,
        end=c.end,
    )


@router.post("", response_model=DeletionResponse)
async def delete_data(
    payload: DeletionRequest,
    request: Request,
    dry_run: bool = True,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_RETENTION)),
) -> DeletionResponse:
    """Preview (``dry_run=true``) or execute a targeted deletion.

    To execute, call again with ``dry_run=false`` and the dry run's ``digest``;
    the confirm is rejected (409) if the data changed since the dry run or if a
    legal hold is active.
    """
    criteria = _criteria(payload)
    try:
        if dry_run:
            preview = await preview_deletion(session, criteria)
            return DeletionResponse(
                executed=False,
                counts=preview.counts,
                affected_days=[d.isoformat() for d in preview.affected_days],
                digest=preview.digest,
                rollups_recomputed=0,
            )

        policy = await resolve_retention_policy(session)
        if policy.legal_hold:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "a legal hold is active; deletion is suspended",
            )
        if payload.digest is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "digest is required to execute a deletion",
            )
        result = await execute_deletion(
            session,
            criteria,
            payload.digest,
            principal.label,
            datetime.now(UTC),
            request.app.state.dialect_name,
            recompute_rollups=payload.recompute_rollups,
        )
        return DeletionResponse(
            executed=True,
            counts=result.counts,
            affected_days=[d.isoformat() for d in result.affected_days],
            digest=result.digest,
            rollups_recomputed=result.rollups_recomputed,
        )
    except EmptyCriteriaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except DeletionDigestMismatchError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
