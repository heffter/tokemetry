"""API token management.

Tokens are random, prefixed, high-entropy strings. Only their SHA-256 hash
is stored; the plaintext is returned exactly once at creation. Third-party
clients (the dashboard, OpenClaw, scripts) authenticate with these.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.scopes import ALL_SCOPES, validate_scopes
from tokemetry_server.security import generate_token, hash_token


@dataclass(frozen=True)
class CreatedToken:
    """A newly minted token; the plaintext is shown only here."""

    label: str
    token: str
    created_at: datetime
    scopes: list[str]


@dataclass(frozen=True)
class TokenInfo:
    """Metadata about a stored token (never includes the secret)."""

    label: str
    created_at: datetime
    last_used: datetime | None
    revoked: bool
    scopes: list[str]
    source_allowlist: list[str] | None


class DuplicateLabelError(ValueError):
    """A token with the requested label already exists."""


async def create_token(
    session: AsyncSession,
    label: str,
    scopes: Iterable[str] | None = None,
    source_allowlist: list[str] | None = None,
) -> CreatedToken:
    """Create and store a new token; return its one-time plaintext.

    ``scopes`` default to the full set for compatibility with pre-scope token
    creation; unknown scopes are rejected (:class:`UnknownScopeError`).

    Raises:
        DuplicateLabelError: If the label is already in use.
        UnknownScopeError: If any requested scope is unknown.
    """
    resolved_scopes = (
        list(ALL_SCOPES) if scopes is None else validate_scopes(scopes)
    )

    existing = await session.execute(
        select(models.ApiToken).where(models.ApiToken.label == label)
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateLabelError(label)

    token = generate_token()
    created_at = datetime.now(UTC)
    session.add(
        models.ApiToken(
            label=label,
            token_hash=hash_token(token),
            created_at=created_at,
            revoked=False,
            scopes=resolved_scopes,
            source_allowlist=source_allowlist,
        )
    )
    return CreatedToken(
        label=label, token=token, created_at=created_at, scopes=resolved_scopes
    )


async def list_tokens(session: AsyncSession) -> list[TokenInfo]:
    """Return metadata for every token, newest first."""
    result = await session.execute(
        select(models.ApiToken).order_by(models.ApiToken.created_at.desc())
    )
    return [
        TokenInfo(
            label=row.label,
            created_at=_as_utc(row.created_at),
            last_used=_as_utc(row.last_used) if row.last_used else None,
            revoked=row.revoked,
            scopes=list(row.scopes or []),
            source_allowlist=list(row.source_allowlist) if row.source_allowlist else None,
        )
        for row in result.scalars()
    ]


async def revoke_token(session: AsyncSession, label: str) -> bool:
    """Revoke the token with ``label``; return True if one was revoked."""
    result = await session.execute(
        select(models.ApiToken).where(models.ApiToken.label == label)
    )
    token = result.scalar_one_or_none()
    if token is None:
        return False
    token.revoked = True
    return True


def _as_utc(value: datetime) -> datetime:
    """Ensure a DB datetime is timezone-aware (UTC)."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
