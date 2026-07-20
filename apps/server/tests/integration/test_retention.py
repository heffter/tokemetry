"""Retention policy model: defaults, override precedence, validation, hold.

Service-level coverage for Task 70.1. Runs against the migrated SQLite session;
the policy is stored in the shared ``app_settings`` KV table, so these also
exercise the override-layering the admin API relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.retention import (
    DEFAULT_RETENTION_POLICY,
    RAW_EVENTS,
    RETENTION_CATEGORIES,
    ROLLUP_VERIFICATION_LAG_DAYS,
    V1_ARCHIVE,
    CategoryRule,
    RetentionPolicy,
    RetentionPolicyError,
    default_policy,
    resolve_retention_policy,
    save_retention_policy,
    validate_retention_policy,
)

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=UTC)


def _policy_with(**overrides: CategoryRule) -> RetentionPolicy:
    """A full valid policy (the defaults) with specific categories replaced."""
    rules = dict(DEFAULT_RETENTION_POLICY.rules)
    rules.update(overrides)
    return RetentionPolicy(rules=rules, legal_hold=False)


def test_default_policy_matches_prd() -> None:
    """The shipped defaults reflect PRD 12.18 (durations and enablement)."""
    rules = DEFAULT_RETENTION_POLICY.rules
    assert rules["raw_events"] == CategoryRule(180, True)
    assert rules["superseded_snapshots"] == CategoryRule(7, True)
    assert rules["daily_rollups"] == CategoryRule(None, True)
    assert rules["limit_snapshots"] == CategoryRule(400, True)
    assert rules["ingest_batches"] == CategoryRule(30, True)
    assert rules["audit_records"] == CategoryRule(400, True)
    assert rules["corrections"] == CategoryRule(None, True)
    assert rules["alert_events"] == CategoryRule(400, True)
    # The renamed v1 archive is retained until an operator opts in.
    assert rules[V1_ARCHIVE] == CategoryRule(None, False)
    assert DEFAULT_RETENTION_POLICY.legal_hold is False


def test_default_policy_returns_independent_copy() -> None:
    """default_policy() hands back a copy, not a shared mutable reference."""
    first = default_policy()
    first.rules["raw_events"] = CategoryRule(1, False)
    assert default_policy().rules["raw_events"] == CategoryRule(180, True)


async def test_resolve_returns_defaults_without_overrides(
    async_session: AsyncSession,
) -> None:
    """With an empty app_settings table, the resolved policy is the default."""
    policy = await resolve_retention_policy(async_session)
    assert policy.rules == DEFAULT_RETENTION_POLICY.rules
    assert policy.legal_hold is False


async def test_override_precedence_and_blank_fallback(
    async_session: AsyncSession,
) -> None:
    """A stored non-empty value wins; a blank value falls back to the default."""
    saved = _policy_with(raw_events=CategoryRule(90, True))
    saved = RetentionPolicy(rules=saved.rules, legal_hold=True)
    await save_retention_policy(async_session, saved, "admin", _NOW)
    await async_session.commit()

    # Blank the raw_events override; resolve must fall back to the default 180.
    row = await async_session.get(models.AppSetting, "retention.raw_events.days")
    assert row is not None
    row.value = ""
    await async_session.commit()

    resolved = await resolve_retention_policy(async_session)
    assert resolved.rules["raw_events"].retention_days == 180  # fell back
    assert resolved.legal_hold is True  # stored override still applied


async def test_indefinite_round_trips(async_session: AsyncSession) -> None:
    """An indefinite (None) duration survives save/resolve via the sentinel."""
    policy = _policy_with(limit_snapshots=CategoryRule(None, True))
    await save_retention_policy(async_session, policy, "admin", _NOW)
    await async_session.commit()
    resolved = await resolve_retention_policy(async_session)
    assert resolved.rules["limit_snapshots"].retention_days is None


def test_validation_rejects_non_positive_days() -> None:
    with pytest.raises(RetentionPolicyError):
        validate_retention_policy(_policy_with(limit_snapshots=CategoryRule(0, True)))


def test_validation_rejects_raw_shorter_than_lag() -> None:
    short = ROLLUP_VERIFICATION_LAG_DAYS - 1
    with pytest.raises(RetentionPolicyError, match="rollup verification lag"):
        validate_retention_policy(_policy_with(raw_events=CategoryRule(short, True)))


def test_validation_rejects_missing_category() -> None:
    rules = dict(DEFAULT_RETENTION_POLICY.rules)
    del rules[RAW_EVENTS]
    with pytest.raises(RetentionPolicyError, match="missing categories"):
        validate_retention_policy(RetentionPolicy(rules=rules, legal_hold=False))


def test_validation_rejects_unknown_category() -> None:
    rules = dict(DEFAULT_RETENTION_POLICY.rules)
    rules["made_up"] = CategoryRule(10, True)
    with pytest.raises(RetentionPolicyError, match="unknown categories"):
        validate_retention_policy(RetentionPolicy(rules=rules, legal_hold=False))


def test_legal_hold_suspends_all_deletion() -> None:
    """Under a legal hold no category is deletion-active, even if enabled."""
    held = RetentionPolicy(rules=dict(DEFAULT_RETENTION_POLICY.rules), legal_hold=True)
    for category in RETENTION_CATEGORIES:
        assert held.is_deletion_active(category) is False


def test_is_deletion_active_respects_enabled_and_indefinite() -> None:
    """Deletion is active only for enabled categories with a finite duration."""
    policy = DEFAULT_RETENTION_POLICY
    assert policy.is_deletion_active("raw_events") is True  # enabled + finite
    assert policy.is_deletion_active("daily_rollups") is False  # indefinite
    assert policy.is_deletion_active(V1_ARCHIVE) is False  # disabled


async def test_save_writes_audit_log(async_session: AsyncSession) -> None:
    """Persisting a policy records a content-free audit entry."""
    await save_retention_policy(async_session, DEFAULT_RETENTION_POLICY, "op", _NOW)
    await async_session.commit()
    rows = (
        await async_session.execute(
            sa.select(models.AuditLog).where(
                models.AuditLog.action == "retention_policy_update"
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].actor == "op"
    assert rows[0].subject == "retention"
    assert rows[0].detail["legal_hold"] is False
    assert rows[0].detail["categories"]["raw_events"]["retention_days"] == 180


async def test_save_rejects_invalid_before_writing(
    async_session: AsyncSession,
) -> None:
    """An invalid policy raises and leaves no app_settings or audit rows."""
    bad = _policy_with(raw_events=CategoryRule(1, True))
    with pytest.raises(RetentionPolicyError):
        await save_retention_policy(async_session, bad, "admin", _NOW)
    settings_count = await async_session.scalar(
        sa.select(sa.func.count()).select_from(models.AppSetting)
    )
    audit_count = await async_session.scalar(
        sa.select(sa.func.count()).select_from(models.AuditLog)
    )
    assert settings_count == 0
    assert audit_count == 0
