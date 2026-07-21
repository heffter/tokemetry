"""Retention policy: per-category durations, enable flags, and legal hold.

The policy names how long each category of record is kept (PRD 12.18 defaults,
Task 70.1). Durations are in whole days; ``None`` means indefinite (never
deleted). Every category can be individually enabled or disabled, and a single
global legal hold suspends *all* deletion regardless of per-category settings
(FR-RET-006).

PRD defaults live in code (:data:`DEFAULT_RETENTION_POLICY`); operators override
them at runtime through the ``app_settings`` KV table under ``retention.*`` keys,
written by the audited ``/api/v2/admin/retention`` endpoint. The resolved policy
is consumed by the retention worker (Task 70.2) and surfaced in operational
status (Task 70.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services import audit

#: Prefix for retention keys in the ``app_settings`` KV table.
KEY_PREFIX = "retention."

#: Sentinel stored/accepted for an indefinite (never-deleted) duration, so a
#: category with no finite retention is distinguishable from "unset".
INDEFINITE = "none"

# Record categories governed by the policy (PRD 12.18). Kept as constants so
# call sites and stored keys never drift from a bare string literal.
RAW_EVENTS = "raw_events"
SUPERSEDED_SNAPSHOTS = "superseded_snapshots"
DAILY_ROLLUPS = "daily_rollups"
LIMIT_SNAPSHOTS = "limit_snapshots"
INGEST_BATCHES = "ingest_batches"
AUDIT_RECORDS = "audit_records"
CORRECTIONS = "corrections"
ALERT_EVENTS = "alert_events"
V1_ARCHIVE = "v1_archive"

#: All categories in a stable, documented order.
RETENTION_CATEGORIES: tuple[str, ...] = (
    RAW_EVENTS,
    SUPERSEDED_SNAPSHOTS,
    DAILY_ROLLUPS,
    LIMIT_SNAPSHOTS,
    INGEST_BATCHES,
    AUDIT_RECORDS,
    CORRECTIONS,
    ALERT_EVENTS,
    V1_ARCHIVE,
)

#: The retention worker verifies that daily rollups cover a day before deleting
#: that day's raw attempt events (FR-RET-004, Task 70.2). Raw-event retention
#: shorter than this lag could delete rows the rollup pipeline has not yet
#: confirmed, so the policy rejects it.
ROLLUP_VERIFICATION_LAG_DAYS = 2


class RetentionPolicyError(ValueError):
    """A retention policy failed validation (nonsensical or unsafe values)."""


@dataclass(frozen=True)
class CategoryRule:
    """Retention rule for one record category.

    ``retention_days`` is a positive whole-day count, or ``None`` for an
    indefinite (never-deleted) category. ``enabled`` gates whether the worker
    deletes this category at all, independent of the duration.
    """

    retention_days: int | None
    enabled: bool

    def deletes(self) -> bool:
        """Whether this rule, on its own, would delete anything."""
        return self.enabled and self.retention_days is not None


@dataclass(frozen=True)
class RetentionPolicy:
    """The full per-category retention policy plus the global legal hold."""

    rules: dict[str, CategoryRule]
    legal_hold: bool

    def rule(self, category: str) -> CategoryRule:
        """Return the rule for ``category`` (KeyError if unknown)."""
        return self.rules[category]

    def is_deletion_active(self, category: str) -> bool:
        """Whether the worker should delete ``category`` right now.

        False under a legal hold (which suspends all deletion), when the
        category is disabled, or when its retention is indefinite.
        """
        if self.legal_hold:
            return False
        return self.rules[category].deletes()


#: PRD 12.18 defaults. ``daily_rollups`` and administrative ``corrections`` are
#: kept indefinitely; the renamed v1 archive (Task 62.10) is retained until an
#: operator opts in, so it ships disabled.
_DEFAULT_RULES: dict[str, CategoryRule] = {
    RAW_EVENTS: CategoryRule(180, True),
    SUPERSEDED_SNAPSHOTS: CategoryRule(7, True),
    DAILY_ROLLUPS: CategoryRule(None, True),
    LIMIT_SNAPSHOTS: CategoryRule(400, True),
    INGEST_BATCHES: CategoryRule(30, True),
    AUDIT_RECORDS: CategoryRule(400, True),
    CORRECTIONS: CategoryRule(None, True),
    ALERT_EVENTS: CategoryRule(400, True),
    V1_ARCHIVE: CategoryRule(None, False),
}

#: The immutable PRD default policy; overrides are layered over a copy of it.
DEFAULT_RETENTION_POLICY = RetentionPolicy(rules=dict(_DEFAULT_RULES), legal_hold=False)


def default_policy() -> RetentionPolicy:
    """Return a fresh copy of the PRD default policy (safe to mutate a copy of)."""
    return RetentionPolicy(rules=dict(_DEFAULT_RULES), legal_hold=False)


def validate_retention_policy(policy: RetentionPolicy) -> None:
    """Raise :class:`RetentionPolicyError` if the policy is nonsensical or unsafe.

    Every finite duration must be at least one day, every category must be
    present, and raw-event retention must not be shorter than the rollup
    verification lag (else the worker could delete unverified raw rows).
    """
    missing = set(RETENTION_CATEGORIES) - set(policy.rules)
    if missing:
        raise RetentionPolicyError(
            "policy missing categories: " + ", ".join(sorted(missing))
        )
    unknown = set(policy.rules) - set(RETENTION_CATEGORIES)
    if unknown:
        raise RetentionPolicyError(
            "policy has unknown categories: " + ", ".join(sorted(unknown))
        )
    for category, rule in policy.rules.items():
        if rule.retention_days is not None and rule.retention_days < 1:
            raise RetentionPolicyError(
                f"{category} retention_days must be >= 1 or null for indefinite"
            )
    raw = policy.rules[RAW_EVENTS]
    if raw.retention_days is not None and raw.retention_days < ROLLUP_VERIFICATION_LAG_DAYS:
        raise RetentionPolicyError(
            f"{RAW_EVENTS} retention_days must be >= the rollup verification lag "
            f"({ROLLUP_VERIFICATION_LAG_DAYS} days)"
        )


def _parse_days(value: str) -> int | None:
    """Parse a stored days value; the indefinite sentinel maps to ``None``."""
    if value.strip().lower() == INDEFINITE:
        return None
    return int(value)


def _parse_bool(value: str) -> bool:
    """Parse a stored boolean the same way channel config coerces flags."""
    return value.strip().lower() in ("1", "true", "yes", "on")


def _days_str(days: int | None) -> str:
    """Serialize a duration for storage (indefinite as the sentinel)."""
    return INDEFINITE if days is None else str(days)


async def resolve_retention_policy(
    session: AsyncSession, base: RetentionPolicy | None = None
) -> RetentionPolicy:
    """Return the default policy with any ``app_settings`` overrides applied.

    A stored ``retention.<category>.days`` / ``.enabled`` value wins over the
    default; ``retention.legal_hold`` toggles the global hold. Blank values are
    ignored so clearing a key reverts to the default.
    """
    base = base or default_policy()
    rows = (
        await session.execute(
            select(models.AppSetting).where(
                models.AppSetting.key.like(f"{KEY_PREFIX}%")
            )
        )
    ).scalars()
    stored = {row.key: row.value for row in rows if row.value != ""}

    rules: dict[str, CategoryRule] = {}
    for category in RETENTION_CATEGORIES:
        rule = base.rules[category]
        days = rule.retention_days
        enabled = rule.enabled
        days_key = f"{KEY_PREFIX}{category}.days"
        enabled_key = f"{KEY_PREFIX}{category}.enabled"
        if days_key in stored:
            days = _parse_days(stored[days_key])
        if enabled_key in stored:
            enabled = _parse_bool(stored[enabled_key])
        rules[category] = CategoryRule(days, enabled)

    legal_hold = base.legal_hold
    legal_hold_key = f"{KEY_PREFIX}legal_hold"
    if legal_hold_key in stored:
        legal_hold = _parse_bool(stored[legal_hold_key])

    return RetentionPolicy(rules=rules, legal_hold=legal_hold)


async def _upsert(session: AsyncSession, key: str, value: str, now: datetime) -> None:
    """Upsert one ``app_settings`` row."""
    existing = await session.get(models.AppSetting, key)
    if existing is None:
        session.add(models.AppSetting(key=key, value=value, updated_at=now))
    else:
        existing.value = value
        existing.updated_at = now


def _policy_detail(policy: RetentionPolicy) -> dict[str, object]:
    """Content-free audit detail: the policy's categories and legal hold."""
    return {
        "legal_hold": policy.legal_hold,
        "categories": {
            category: {
                "retention_days": policy.rules[category].retention_days,
                "enabled": policy.rules[category].enabled,
            }
            for category in RETENTION_CATEGORIES
        },
    }


async def save_retention_policy(
    session: AsyncSession, policy: RetentionPolicy, actor: str | None, now: datetime
) -> RetentionPolicy:
    """Validate and persist ``policy`` to ``app_settings``, then audit the change.

    Returns the saved policy. Raises :class:`RetentionPolicyError` (before any
    write) if the policy is invalid.
    """
    validate_retention_policy(policy)
    for category in RETENTION_CATEGORIES:
        rule = policy.rules[category]
        await _upsert(
            session, f"{KEY_PREFIX}{category}.days", _days_str(rule.retention_days), now
        )
        await _upsert(
            session,
            f"{KEY_PREFIX}{category}.enabled",
            "true" if rule.enabled else "false",
            now,
        )
    await _upsert(
        session,
        f"{KEY_PREFIX}legal_hold",
        "true" if policy.legal_hold else "false",
        now,
    )
    audit.record(
        session,
        actor=actor,
        action="retention_policy_update",
        subject="retention",
        detail=_policy_detail(policy),
        ts=now,
    )
    return policy
