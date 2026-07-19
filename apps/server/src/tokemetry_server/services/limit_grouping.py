"""Limit-stream grouping (FR-LIMIT-005).

A limit stream is a distinct series of snapshots for one limit, keyed by
provider, window kind, account, organization, and reporting source. Different
keys are never merged into one series without an explicit configured grouping
rule, so two accounts' identically-named windows stay separate rather than
silently averaging together. Provenance (official vs estimated, FR-LIMIT-004)
stays on each snapshot and is never part of the key, so a stream can carry both
an official reading and an estimate over time without splitting.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tokemetry_server.db import models


@dataclass(frozen=True)
class LimitStreamKey:
    """The identity of one limit stream (FR-LIMIT-005)."""

    provider: str
    window_kind: str
    account: str | None
    organization: str | None
    source_id: int | None


def stream_key(snapshot: models.LimitSnapshot) -> LimitStreamKey:
    """The stream a snapshot belongs to."""
    return LimitStreamKey(
        provider=snapshot.provider,
        window_kind=snapshot.window_kind,
        account=snapshot.account,
        organization=snapshot.organization,
        source_id=snapshot.source_id,
    )


def group_limit_snapshots(
    snapshots: Iterable[models.LimitSnapshot],
) -> dict[LimitStreamKey, list[models.LimitSnapshot]]:
    """Group snapshots into distinct streams, preserving input order per stream.

    This is the default (no-merge) rule: each distinct
    ``(provider, window_kind, account, organization, source_id)`` is its own
    series. Merging two keys requires an explicit configured rule, which this
    function deliberately does not apply.
    """
    grouped: dict[LimitStreamKey, list[models.LimitSnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(stream_key(snapshot), []).append(snapshot)
    return grouped
