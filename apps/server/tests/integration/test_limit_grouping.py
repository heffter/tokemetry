"""Limit-stream grouping rule (Task 69.2, FR-LIMIT-005)."""

from __future__ import annotations

from datetime import UTC, datetime

from tokemetry_server.db import models
from tokemetry_server.services.limit_grouping import (
    group_limit_snapshots,
    stream_key,
)

_TS = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


def _snapshot(**fields: object) -> models.LimitSnapshot:
    defaults: dict[str, object] = {
        "provider": "anthropic",
        "machine": None,
        "ts": _TS,
        "window_kind": "five_hour",
        "utilization_pct": 50,
        "resets_at": None,
        "provenance": "official",
        "account": None,
        "organization": None,
        "source_id": None,
        "limit_amount": None,
        "remaining": None,
        "unit": None,
        "raw": {},
    }
    defaults.update(fields)
    return models.LimitSnapshot(**defaults)


def test_two_accounts_on_the_same_window_are_two_streams() -> None:
    # FR-LIMIT-005: identically-named windows for different accounts must not
    # merge into one series.
    grouped = group_limit_snapshots(
        [
            _snapshot(account="acct-a", utilization_pct=40),
            _snapshot(account="acct-b", utilization_pct=90),
        ]
    )
    assert len(grouped) == 2
    keys = {k.account for k in grouped}
    assert keys == {"acct-a", "acct-b"}


def test_same_stream_keeps_official_and_estimated_together() -> None:
    # FR-LIMIT-004: provenance is not part of the key, so an official reading
    # and an estimate for the same window/account stay in one stream.
    grouped = group_limit_snapshots(
        [
            _snapshot(account="acct-a", provenance="official"),
            _snapshot(account="acct-a", provenance="estimated"),
        ]
    )
    assert len(grouped) == 1
    (snapshots,) = grouped.values()
    assert {s.provenance for s in snapshots} == {"official", "estimated"}


def test_source_and_organization_distinguish_streams() -> None:
    grouped = group_limit_snapshots(
        [
            _snapshot(organization="org-1", source_id=1),
            _snapshot(organization="org-1", source_id=2),
            _snapshot(organization="org-2", source_id=1),
        ]
    )
    assert len(grouped) == 3


def test_stream_key_captures_the_five_dimensions() -> None:
    key = stream_key(
        _snapshot(
            provider="openai",
            window_kind="weekly",
            account="a",
            organization="o",
            source_id=7,
        )
    )
    assert key.provider == "openai"
    assert key.window_kind == "weekly"
    assert (key.account, key.organization, key.source_id) == ("a", "o", 7)
