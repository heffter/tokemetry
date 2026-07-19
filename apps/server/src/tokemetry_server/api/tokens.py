"""API token management routes.

Minting, listing, and revoking bearer tokens for third-party clients. The
plaintext token is returned only once, at creation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import require_token
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.schemas_query import (
    TokenCreatedOut,
    TokenCreateRequest,
    TokenInfoOut,
)
from tokemetry_server.scopes import UnknownScopeError
from tokemetry_server.services import tokens as token_service

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


@router.post("", response_model=TokenCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: TokenCreateRequest,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> TokenCreatedOut:
    """Mint a new API token; the secret is returned only in this response."""
    try:
        created = await token_service.create_token(
            session, payload.label, payload.scopes, payload.source_allowlist
        )
    except token_service.DuplicateLabelError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="label already exists"
        ) from exc
    except UnknownScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return TokenCreatedOut(
        label=created.label,
        token=created.token,
        created_at=created.created_at,
        scopes=created.scopes,
    )


@router.get("", response_model=list[TokenInfoOut])
async def list_tokens(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[TokenInfoOut]:
    """List token metadata (never the secrets)."""
    return [
        TokenInfoOut(
            label=info.label,
            created_at=info.created_at,
            last_used=info.last_used,
            revoked=info.revoked,
            scopes=info.scopes,
            source_allowlist=info.source_allowlist,
        )
        for info in await token_service.list_tokens(session)
    ]


@router.delete("/{label}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    label: str,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> Response:
    """Revoke a token by label."""
    revoked = await token_service.revoke_token(session, label)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown label")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
