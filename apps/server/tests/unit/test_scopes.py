"""Unit tests for the token scope vocabulary."""

from __future__ import annotations

import pytest
from tokemetry_server.scopes import (
    ADMIN_TOKENS,
    ALL_SCOPES,
    INGEST_EVENTS,
    KNOWN_SCOPES,
    QUERY_READ,
    UnknownScopeError,
    validate_scopes,
)


def test_all_scopes_are_known() -> None:
    assert set(ALL_SCOPES) == KNOWN_SCOPES
    assert len(ALL_SCOPES) == len(set(ALL_SCOPES))  # no duplicates


def test_validate_returns_canonical_order() -> None:
    # Input order and duplicates do not matter; output is canonical.
    result = validate_scopes([QUERY_READ, INGEST_EVENTS, INGEST_EVENTS])
    assert result == [INGEST_EVENTS, QUERY_READ]


def test_validate_rejects_unknown_scope() -> None:
    with pytest.raises(UnknownScopeError, match="ingest:everything"):
        validate_scopes([INGEST_EVENTS, "ingest:everything"])


def test_validate_empty_is_allowed() -> None:
    assert validate_scopes([]) == []


def test_full_set_validates() -> None:
    assert validate_scopes(ALL_SCOPES) == list(ALL_SCOPES)
    assert ADMIN_TOKENS in validate_scopes(ALL_SCOPES)
