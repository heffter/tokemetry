"""v2 retention administration: read and update the retention policy (Task 70.1).

Both operations require the ``admin:retention`` scope. GET returns the resolved
policy (PRD defaults with any stored overrides); PUT validates and persists a
full policy, writing an audit entry. The policy governs the retention worker
(Task 70.2) and the administrative deletion APIs (Task 70.3).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import Principal, require_scopes
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.v2.schemas import (
    RetentionCategoryConfig,
    RetentionCategoryStatus,
    RetentionPolicyBody,
    RetentionStatusResponse,
)
from tokemetry_server.db import models
from tokemetry_server.scopes import ADMIN_RETENTION
from tokemetry_server.services.retention import (
    RETENTION_CATEGORIES,
    CategoryRule,
    RetentionPolicy,
    RetentionPolicyError,
    resolve_retention_policy,
    save_retention_policy,
)

router = APIRouter(prefix="/api/v2/admin/retention", tags=["retention"])


def _to_body(policy: RetentionPolicy) -> RetentionPolicyBody:
    """Serialize a domain policy to its wire form."""
    return RetentionPolicyBody(
        categories={
            category: RetentionCategoryConfig(
                retention_days=policy.rules[category].retention_days,
                enabled=policy.rules[category].enabled,
            )
            for category in RETENTION_CATEGORIES
        },
        legal_hold=policy.legal_hold,
    )


def _from_body(body: RetentionPolicyBody) -> RetentionPolicy:
    """Build a domain policy from a wire body, rejecting unknown/missing keys."""
    known = set(RETENTION_CATEGORIES)
    provided = set(body.categories)
    if provided != known:
        missing = ", ".join(sorted(known - provided))
        unknown = ", ".join(sorted(provided - known))
        detail = "categories must name every known category exactly"
        if missing:
            detail += f"; missing: {missing}"
        if unknown:
            detail += f"; unknown: {unknown}"
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)
    return RetentionPolicy(
        rules={
            category: CategoryRule(
                retention_days=body.categories[category].retention_days,
                enabled=body.categories[category].enabled,
            )
            for category in RETENTION_CATEGORIES
        },
        legal_hold=body.legal_hold,
    )


@router.get("", response_model=RetentionPolicyBody)
async def get_retention_policy(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(ADMIN_RETENTION)),
) -> RetentionPolicyBody:
    """Return the resolved retention policy (defaults plus stored overrides)."""
    return _to_body(await resolve_retention_policy(session))


@router.put("", response_model=RetentionPolicyBody)
async def put_retention_policy(
    body: RetentionPolicyBody,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(require_scopes(ADMIN_RETENTION)),
) -> RetentionPolicyBody:
    """Validate and persist a full retention policy, auditing the change."""
    policy = _from_body(body)
    try:
        saved = await save_retention_policy(
            session, policy, principal.label, datetime.now(UTC)
        )
    except RetentionPolicyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return _to_body(saved)


@router.get("/status", response_model=RetentionStatusResponse)
async def get_retention_status(
    session: AsyncSession = Depends(get_session),
    _: Principal = Depends(require_scopes(ADMIN_RETENTION)),
) -> RetentionStatusResponse:
    """Return each category's policy plus its last retention-worker outcome."""
    policy = await resolve_retention_policy(session)
    rows = {
        row.category: row
        for row in (
            await session.execute(select(models.RetentionStatus))
        ).scalars()
    }
    categories = []
    for category in RETENTION_CATEGORIES:
        rule = policy.rules[category]
        st = rows.get(category)
        categories.append(
            RetentionCategoryStatus(
                category=category,
                retention_days=rule.retention_days,
                enabled=rule.enabled,
                last_run_at=st.last_run_at if st else None,
                last_deleted=st.last_deleted if st else 0,
                total_deleted=st.total_deleted if st else 0,
                pending_backlog=st.pending_backlog if st else 0,
                oldest_retained=st.oldest_retained if st else None,
            )
        )
    return RetentionStatusResponse(legal_hold=policy.legal_hold, categories=categories)
