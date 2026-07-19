"""Reviewable, audited rate-card imports (D-015, FR-PRICE-015/016).

A price import never silently rewrites stored rates. It is a two-step flow: a
dry run diffs the incoming :class:`~tokemetry_core.pricing.sources.rate_card.RateCardRow`
set against the stored ``rate_cards`` and returns a structured diff plus a
content ``digest`` without persisting; apply recomputes the diff, requires the
caller's digest to match (so a change to the stored rates between the two calls
is rejected), and only then closes superseded rows and inserts new ones, writing
an audit entry.

Past effective periods are never rewritten (FR-PRICE-016): a changed current
price closes the prior open row with ``effective_to = effective_from - 1 day``
and opens a new row effective from the import date. A row whose stored current
card is already effective on or after the import date is reported as a conflict
and left untouched, rather than silently overwriting that period.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.pricing.sources.rate_card import RateCardRow

from tokemetry_server.db import models

#: Diff actions for a single incoming rate-card row.
ACTION_NEW = "new"
ACTION_SUPERSEDED = "superseded"
ACTION_UNCHANGED = "unchanged"
ACTION_CONFLICT = "conflict"


class DigestMismatchError(ValueError):
    """The apply digest does not match the current dry-run diff (stale import)."""


@dataclass(frozen=True)
class RateCardChange:
    """One incoming row's effect on the stored rate cards."""

    action: str
    provider: str
    native_model: str
    unit_type: str
    priority: int
    new_price: Decimal | None
    old_card_id: int | None
    close_effective_to: date | None
    reason: str | None = None


@dataclass(frozen=True)
class ImportDiff:
    """The full diff of an incoming rate-card set, with a content digest."""

    changes: tuple[RateCardChange, ...]
    digest: str

    def _count(self, action: str) -> int:
        return sum(1 for change in self.changes if change.action == action)

    @property
    def new_count(self) -> int:
        """Number of rows with no stored card on their grain."""
        return self._count(ACTION_NEW)

    @property
    def superseded_count(self) -> int:
        """Number of rows that close a prior open card and open a new one."""
        return self._count(ACTION_SUPERSEDED)

    @property
    def unchanged_count(self) -> int:
        """Number of rows whose price already matches the stored current card."""
        return self._count(ACTION_UNCHANGED)

    @property
    def conflict_count(self) -> int:
        """Number of rows whose stored card is effective on/after the import date."""
        return self._count(ACTION_CONFLICT)


@dataclass(frozen=True)
class ImportResult:
    """The outcome of applying an import."""

    applied_new: int
    applied_superseded: int
    conflicts: int
    unchanged: int
    digest: str
    changes: tuple[RateCardChange, ...]


async def _current_open_card(
    session: AsyncSession, row: RateCardRow
) -> models.RateCard | None:
    """The stored open (unclosed) card on ``row``'s grain, latest first."""
    card = models.RateCard
    stmt = select(card).where(
        card.provider == row.provider,
        card.native_model == row.native_model,
        card.unit_type == row.unit_type,
        card.mode == row.mode,
        card.priority == row.priority,
        card.effective_to.is_(None),
        card.service_tier == row.service_tier
        if row.service_tier is not None
        else card.service_tier.is_(None),
        card.context_bracket == row.context_bracket
        if row.context_bracket is not None
        else card.context_bracket.is_(None),
    ).order_by(card.effective_from.desc())
    return (await session.execute(stmt)).scalars().first()


def _change(
    row: RateCardRow,
    action: str,
    old_card_id: int | None,
    close_effective_to: date | None,
    reason: str | None = None,
) -> RateCardChange:
    """Build a change record from an incoming row and a decided action."""
    return RateCardChange(
        action=action,
        provider=row.provider,
        native_model=row.native_model,
        unit_type=row.unit_type,
        priority=row.priority,
        new_price=row.unit_price,
        old_card_id=old_card_id,
        close_effective_to=close_effective_to,
        reason=reason,
    )


def _classify(
    row: RateCardRow, existing: models.RateCard | None, effective_from: date
) -> RateCardChange:
    """Decide what an incoming row does to the stored rate cards."""
    if existing is None:
        return _change(row, ACTION_NEW, None, None)
    if existing.unit_price == row.unit_price:
        return _change(row, ACTION_UNCHANGED, existing.id, None)
    if existing.effective_from < effective_from:
        return _change(
            row, ACTION_SUPERSEDED, existing.id, effective_from - timedelta(days=1)
        )
    return _change(
        row,
        ACTION_CONFLICT,
        existing.id,
        None,
        reason="stored card is effective on or after the import date",
    )


def _digest(changes: tuple[RateCardChange, ...]) -> str:
    """A deterministic sha256 over the diff, for stale-apply detection."""
    canonical = json.dumps(
        [
            [
                change.action,
                change.provider,
                change.native_model,
                change.unit_type,
                change.priority,
                str(change.new_price) if change.new_price is not None else None,
                change.old_card_id,
                change.close_effective_to.isoformat()
                if change.close_effective_to is not None
                else None,
            ]
            for change in sorted(
                changes,
                key=lambda c: (c.provider, c.native_model, c.unit_type, c.priority),
            )
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def compute_import_diff(
    session: AsyncSession, rows: list[RateCardRow], effective_from: date
) -> ImportDiff:
    """Diff an incoming rate-card set against the stored cards (no persistence)."""
    changes: list[RateCardChange] = []
    for row in rows:
        existing = await _current_open_card(session, row)
        changes.append(_classify(row, existing, effective_from))
    ordered = tuple(changes)
    return ImportDiff(changes=ordered, digest=_digest(ordered))


def _to_rate_card(row: RateCardRow, now: datetime) -> models.RateCard:
    """Map an incoming row onto a new ``rate_cards`` ORM row."""
    return models.RateCard(
        provider=row.provider,
        native_model=row.native_model,
        unit_type=row.unit_type,
        effective_from=row.effective_from,
        effective_to=None,
        currency=row.currency,
        region=row.region,
        service_tier=row.service_tier,
        mode=row.mode,
        context_bracket=row.context_bracket,
        unit_price=row.unit_price,
        source=row.source,
        verified_at=row.verified_at,
        priority=row.priority,
        override=row.override,
        created_at=now,
    )


async def apply_import(
    session: AsyncSession,
    rows: list[RateCardRow],
    effective_from: date,
    expected_digest: str,
    actor: str | None,
    source_label: str,
    now: datetime | None = None,
) -> ImportResult:
    """Apply an import after verifying the caller's digest; audited.

    Raises:
        DigestMismatchError: If the recomputed diff's digest differs from
            ``expected_digest`` (the stored rates changed since the dry run).
    """
    stamp = now if now is not None else datetime.now(UTC)
    diff = await compute_import_diff(session, rows, effective_from)
    if diff.digest != expected_digest:
        raise DigestMismatchError(
            "import digest does not match the current rate cards; re-run the dry run"
        )
    applied_new = applied_superseded = conflicts = unchanged = 0
    for row, change in zip(rows, diff.changes, strict=True):
        if change.action == ACTION_NEW:
            session.add(_to_rate_card(row, stamp))
            applied_new += 1
        elif change.action == ACTION_SUPERSEDED:
            await session.execute(
                update(models.RateCard)
                .where(models.RateCard.id == change.old_card_id)
                .values(effective_to=change.close_effective_to)
            )
            session.add(_to_rate_card(row, stamp))
            applied_superseded += 1
        elif change.action == ACTION_UNCHANGED:
            unchanged += 1
        else:  # conflict: never rewrite the stored period silently
            conflicts += 1

    session.add(
        models.AuditLog(
            actor=actor,
            action="pricing_import",
            subject=source_label,
            detail={
                "source": source_label,
                "digest": diff.digest,
                "effective_from": effective_from.isoformat(),
                "new": applied_new,
                "superseded": applied_superseded,
                "conflicts": conflicts,
                "unchanged": unchanged,
            },
            ts=stamp,
        )
    )
    return ImportResult(
        applied_new=applied_new,
        applied_superseded=applied_superseded,
        conflicts=conflicts,
        unchanged=unchanged,
        digest=diff.digest,
        changes=diff.changes,
    )
