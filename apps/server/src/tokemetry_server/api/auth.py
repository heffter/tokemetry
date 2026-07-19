"""Bearer-token authentication and scope authorization.

A request is authenticated if its bearer token matches either the configured
bootstrap token (which implicitly holds every scope, FR-SEC-008) or a
non-revoked ``api_tokens`` row. Successful database-token use refreshes
``last_used``. Authentication failures always return a uniform 401 that never
reveals whether a token label exists (FR-SEC-010).

Authorization is scope-based (task 63.4): :func:`require_scopes` builds a
dependency that returns the authenticated :class:`Principal` only when it holds
every required scope, otherwise 403. Ingest-only tokens therefore receive 403 on
query endpoints (FR-INGEST-004).
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.scopes import ALL_SCOPES
from tokemetry_server.security import hash_token

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid bearer token",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class Principal:
    """The authenticated caller: its label, scopes, and optional allowlist."""

    label: str
    scopes: frozenset[str]
    source_allowlist: list[str] | None
    is_bootstrap: bool

    def has_scope(self, scope: str) -> bool:
        """Whether this principal holds ``scope``."""
        return scope in self.scopes


def _matches_bootstrap(token: str, settings: Settings) -> bool:
    """Constant-time comparison against the configured bootstrap token."""
    configured = settings.api_bootstrap_token
    if not configured:
        return False
    return hmac.compare_digest(token, configured)


async def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Authenticate the request, returning the caller :class:`Principal`.

    Raises:
        HTTPException: 401 when no valid token is presented.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    token = credentials.credentials
    settings: Settings = request.app.state.settings

    if _matches_bootstrap(token, settings):
        return Principal(
            label="bootstrap",
            scopes=frozenset(ALL_SCOPES),
            source_allowlist=None,
            is_bootstrap=True,
        )

    session_factory = request.app.state.session_factory
    token_hash = hash_token(token)
    async with session_factory() as session:
        principal = await _consume_db_token(session, token_hash)
        if principal is None:
            raise _UNAUTHORIZED
        await session.commit()
        return principal


def require_scopes(*required: str) -> Callable[[Principal], Awaitable[Principal]]:
    """Build a dependency requiring the caller to hold every named scope.

    Returns the :class:`Principal` on success; raises 403 when any scope is
    missing (FR-INGEST-004). Authentication (401) is checked first via
    :func:`require_token`.
    """
    needed = frozenset(required)

    async def _dependency(principal: Principal = Depends(require_token)) -> Principal:
        if not needed <= principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient scope"
            )
        return principal

    return _dependency


async def _consume_db_token(session: AsyncSession, token_hash: str) -> Principal | None:
    """Return the principal for a valid token hash and refresh last_used."""
    result = await session.execute(
        select(models.ApiToken).where(
            models.ApiToken.token_hash == token_hash,
            models.ApiToken.revoked.is_(False),
        )
    )
    token_row = result.scalar_one_or_none()
    if token_row is None:
        return None
    token_row.last_used = datetime.now(UTC)
    return Principal(
        label=token_row.label,
        scopes=frozenset(token_row.scopes or []),
        source_allowlist=list(token_row.source_allowlist)
        if token_row.source_allowlist
        else None,
        is_bootstrap=False,
    )
