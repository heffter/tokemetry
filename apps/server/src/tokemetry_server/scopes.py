"""API token scope vocabulary (FR-INGEST-003, decisions D-002/D-015).

Least-privilege scopes for bearer tokens. Ingest scopes gate the write
surfaces, ``query:read`` gates the read surfaces, and the ``admin:*`` scopes
gate the administrative surfaces (token management, corrections, and the
pricing/retention admin landing in Tasks 64 and 70). The env bootstrap token
implicitly holds every scope (FR-SEC-008); it is never represented as a row.
"""

from __future__ import annotations

from collections.abc import Iterable

INGEST_EVENTS = "ingest:events"
INGEST_LIMITS = "ingest:limits"
INGEST_AGGREGATES = "ingest:aggregates"
QUERY_READ = "query:read"
ADMIN_TOKENS = "admin:tokens"
ADMIN_CORRECTIONS = "admin:corrections"
ADMIN_PRICING = "admin:pricing"
ADMIN_RETENTION = "admin:retention"

#: The full scope set, in a stable order (the compatibility default that
#: existing tokens receive on upgrade so current collectors keep working).
ALL_SCOPES: tuple[str, ...] = (
    INGEST_EVENTS,
    INGEST_LIMITS,
    INGEST_AGGREGATES,
    QUERY_READ,
    ADMIN_TOKENS,
    ADMIN_CORRECTIONS,
    ADMIN_PRICING,
    ADMIN_RETENTION,
)

#: Every recognized scope; anything else is rejected at token creation.
KNOWN_SCOPES: frozenset[str] = frozenset(ALL_SCOPES)


class UnknownScopeError(ValueError):
    """One or more requested scopes are not in the known vocabulary."""


def validate_scopes(scopes: Iterable[str]) -> list[str]:
    """Return the scopes de-duplicated in canonical order, rejecting unknowns.

    Raises:
        UnknownScopeError: If any scope is not in :data:`KNOWN_SCOPES`.
    """
    requested = set(scopes)
    unknown = requested - KNOWN_SCOPES
    if unknown:
        raise UnknownScopeError(f"unknown scopes: {sorted(unknown)}")
    return [scope for scope in ALL_SCOPES if scope in requested]
