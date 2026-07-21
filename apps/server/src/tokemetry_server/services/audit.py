"""Shared audit log: the single write path for administrative actions (Task 70.4).

Every administrative mutation -- repricing, price imports, rate-card changes,
retention-policy edits, targeted deletions, token create/revoke -- records an
entry through :func:`record`, so the audit trail is written one way and stays
content-free: ``detail`` carries filters, counts, and versions, never secrets or
usage content (NFR-SEC-005, FR-PRIV-009/011). The log is append-only; there is
no delete path, and it ages out only under the ``audit_records`` retention
category (400-day default).

:func:`list_audit` is the read side behind ``GET /api/v2/admin/audit``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models


def record(
    session: AsyncSession,
    *,
    actor: str | None,
    action: str,
    ts: datetime,
    subject: str | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> models.AuditLog:
    """Append one audit entry (caller owns the transaction).

    ``detail`` must be content-free metadata only -- never a secret or usage
    content. Returns the added row (not yet flushed).
    """
    row = models.AuditLog(
        actor=actor,
        action=action,
        subject=subject,
        detail=detail if detail is not None else {},
        ts=ts,
        request_id=request_id,
    )
    session.add(row)
    return row


async def list_audit(
    session: AsyncSession,
    *,
    action: str | None = None,
    actor: str | None = None,
    limit: int = 100,
) -> list[models.AuditLog]:
    """Return recent audit entries, newest first, with optional filters."""
    stmt = select(models.AuditLog).order_by(
        models.AuditLog.ts.desc(), models.AuditLog.id.desc()
    )
    if action is not None:
        stmt = stmt.where(models.AuditLog.action == action)
    if actor is not None:
        stmt = stmt.where(models.AuditLog.actor == actor)
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars())
