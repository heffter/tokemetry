"""Bearer-token authentication dependency.

Every API route depends on :func:`require_token`. A request is authorized
if its bearer token matches either the configured bootstrap token (for
first-run collector setup) or a non-revoked row in ``api_tokens`` (looked up
by hash). Successful database-token use refreshes ``last_used``.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.config import Settings
from tokemetry_server.db import models
from tokemetry_server.security import hash_token

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid bearer token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _matches_bootstrap(token: str, settings: Settings) -> bool:
    """Constant-time comparison against the configured bootstrap token."""
    configured = settings.api_bootstrap_token
    if not configured:
        return False
    return hmac.compare_digest(token, configured)


async def require_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Authorize the request, returning a label for the caller.

    Raises:
        HTTPException: 401 when no valid token is presented.
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    token = credentials.credentials
    settings: Settings = request.app.state.settings

    if _matches_bootstrap(token, settings):
        return "bootstrap"

    session_factory = request.app.state.session_factory
    token_hash = hash_token(token)
    async with session_factory() as session:
        label = await _consume_db_token(session, token_hash)
        if label is None:
            raise _UNAUTHORIZED
        await session.commit()
        return label


async def _consume_db_token(session: AsyncSession, token_hash: str) -> str | None:
    """Return the label for a valid token hash and refresh last_used."""
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
    return token_row.label
