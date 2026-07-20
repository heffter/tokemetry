"""Alert engine: evaluate enabled rules and dispatch notifications.

For each enabled rule the engine honors quiet hours and a per-rule cooldown,
evaluates the condition, dispatches any finding to the rule's channels, and
records an ``alert_events`` row (whether or not delivery succeeded, so the
history is complete).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.alerting.notifiers import Notifier
from tokemetry_server.services.alerting.rules import (
    AlertFinding,
    EntityFinding,
    evaluate_rule,
    evaluate_stale_sources,
    is_grouped_kind,
)
from tokemetry_server.services.sources import DEFAULT_STALE_SECONDS

#: Firing-state values stored on ``AlertRule.state`` and per-entity states.
_FIRING = "firing"
_NORMAL = "normal"


@dataclass(frozen=True)
class FiredAlert:
    """Result of a rule that fired (or resolved) during an evaluation pass."""

    rule_name: str
    finding: AlertFinding
    delivered: bool


def _resolved_finding(rule: models.AlertRule) -> AlertFinding:
    """Build the one-off recovery notice for a firing->normal transition."""
    return AlertFinding(
        severity="info",
        title=f"Resolved: {rule.name}",
        body=f"The condition for '{rule.name}' has cleared.",
        context={"resolved": True},
    )


class AlertEngine:
    """Evaluates alert rules and dispatches through notification channels."""

    def __init__(
        self,
        notifiers: dict[str, Notifier],
        timezone: str = "UTC",
        stale_thresholds: dict[str, float] | None = None,
        default_stale_seconds: float = DEFAULT_STALE_SECONDS,
    ) -> None:
        """Create the engine over a channel-name -> notifier registry.

        ``timezone`` is the IANA name in which quiet hours are evaluated; an
        unknown name falls back to UTC. ``stale_thresholds`` and
        ``default_stale_seconds`` are the per-source-type staleness thresholds
        (Task 63.2) used as the defaults for ``stale_source`` rules.
        """
        self._notifiers = notifiers
        self._tz = _resolve_zone(timezone)
        self._stale_thresholds = stale_thresholds
        self._default_stale_seconds = default_stale_seconds

    def reconfigure(self, notifiers: dict[str, Notifier]) -> None:
        """Swap the notifier registry (after channel settings change)."""
        self._notifiers = notifiers

    async def run(
        self, session: AsyncSession, now: datetime | None = None
    ) -> list[FiredAlert]:
        """Evaluate all enabled rules, dispatch, and record events."""
        reference = now if now is not None else datetime.now(UTC)
        rules = (
            await session.execute(
                select(models.AlertRule).where(models.AlertRule.enabled.is_(True))
            )
        ).scalars()

        fired: list[FiredAlert] = []
        for rule in rules:
            if _in_quiet_hours(rule, reference, self._tz):
                continue
            if is_grouped_kind(rule.kind):
                await self._run_grouped(session, rule, reference, fired)
                continue
            finding = await evaluate_rule(session, rule, reference)
            if finding is not None:
                # Notify on the transition into firing, or on a repeat once the
                # cooldown has elapsed. Cooldown never suppresses the first fire.
                is_new = rule.state != _FIRING
                if is_new or not await self._in_cooldown(session, rule, reference):
                    delivered = await self._dispatch(rule, finding)
                    self._record(session, rule, reference, finding, delivered)
                    rule.last_fired_at = reference
                    fired.append(FiredAlert(rule.name, finding, delivered))
                rule.state = _FIRING
            elif rule.state == _FIRING:
                # firing -> normal: send one recovery notice, ignoring cooldown.
                resolved = _resolved_finding(rule)
                delivered = await self._dispatch(rule, resolved)
                self._record(session, rule, reference, resolved, delivered)
                rule.state = _NORMAL
                fired.append(FiredAlert(rule.name, resolved, delivered))
        return fired

    async def _run_grouped(
        self,
        session: AsyncSession,
        rule: models.AlertRule,
        now: datetime,
        fired: list[FiredAlert],
    ) -> None:
        """Evaluate a per-entity rule, tracking firing state for each entity.

        Every affected entity fires (and re-fires only after its own cooldown)
        and resolves independently, so multiple stale sources notify without
        suppressing one another (FR-ALERT-003, FR-SOURCE-007). Per-entity state
        lives in ``rule.entity_state``.
        """
        findings = await self._evaluate_grouped(session, rule, now)
        state: dict[str, Any] = dict(rule.entity_state or {})
        active_keys = {ef.key for ef in findings}
        fires = 0

        for entity in findings:
            prev = state.get(entity.key)
            if (
                isinstance(prev, dict)
                and prev.get("state") == _FIRING
                and self._entity_in_cooldown(prev, rule, now)
            ):
                continue  # already firing and still inside its cooldown
            delivered = await self._dispatch(rule, entity.finding)
            self._record(session, rule, now, entity.finding, delivered)
            fired.append(FiredAlert(rule.name, entity.finding, delivered))
            state[entity.key] = {"state": _FIRING, "last_fired_at": now.isoformat()}
            fires += 1

        for key, prev in list(state.items()):
            if not (isinstance(prev, dict) and prev.get("state") == _FIRING):
                continue
            if key in active_keys:
                continue
            resolved = await self._resolved_entity(session, key)
            if resolved is None:
                del state[key]  # revoked or removed: not a recovery, clear silently
                continue
            delivered = await self._dispatch(rule, resolved)
            self._record(session, rule, now, resolved, delivered)
            fired.append(FiredAlert(rule.name, resolved, delivered))
            state[key] = {"state": _NORMAL, "last_fired_at": prev.get("last_fired_at")}

        rule.entity_state = state
        rule.state = _FIRING if active_keys else _NORMAL
        if fires:
            rule.last_fired_at = now

    async def _evaluate_grouped(
        self, session: AsyncSession, rule: models.AlertRule, now: datetime
    ) -> list[EntityFinding]:
        """Dispatch a grouped rule kind to its per-entity evaluator."""
        return await evaluate_stale_sources(
            session,
            rule,
            now,
            stale_thresholds=self._stale_thresholds,
            default_stale_seconds=self._default_stale_seconds,
        )

    def _entity_in_cooldown(
        self, prev: dict[str, Any], rule: models.AlertRule, now: datetime
    ) -> bool:
        """True when an entity last fired within the rule's cooldown window."""
        raw = prev.get("last_fired_at")
        if not isinstance(raw, str):
            return False
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            return False
        last_aware = last if last.tzinfo else last.replace(tzinfo=UTC)
        return (now - last_aware).total_seconds() < rule.cooldown_seconds

    async def _resolved_entity(
        self, session: AsyncSession, key: str
    ) -> AlertFinding | None:
        """Build the recovery notice for a source, or None to clear state silently.

        A source that is gone or revoked is not a recovery -- a deliberate
        revocation should not read as "ingesting again" -- so its firing state is
        cleared without a resolved notice.
        """
        try:
            source_id = int(key)
        except ValueError:
            return None
        source = await session.get(models.Source, source_id)
        if source is None or source.revoked:
            return None
        return AlertFinding(
            severity="info",
            title=f"Resolved: source {source.name}",
            body=f"Source '{source.name}' ({source.type}) is ingesting again.",
            context={
                "resolved": True,
                "source_id": source.id,
                "source_type": source.type,
                "source_name": source.name,
            },
        )

    def _record(
        self,
        session: AsyncSession,
        rule: models.AlertRule,
        now: datetime,
        finding: AlertFinding,
        delivered: bool,
    ) -> None:
        """Append an alert_events row for a fired or resolved finding."""
        session.add(
            models.AlertEvent(
                rule_id=rule.id,
                ts=now,
                severity=finding.severity,
                title=finding.title,
                body=finding.body,
                delivered=delivered,
                context=finding.context,
            )
        )

    async def test_channel(self, channel: str) -> bool:
        """Send a canned test notification through one channel.

        Returns False when the channel is unknown or unconfigured, so the UI
        can distinguish a misconfiguration from a delivery failure.
        """
        notifier = self._notifiers.get(channel)
        if notifier is None or not notifier.is_configured():
            return False
        return await notifier.send(
            "tokemetry test", "Test notification from tokemetry.", "info"
        )

    async def _in_cooldown(
        self, session: AsyncSession, rule: models.AlertRule, now: datetime
    ) -> bool:
        """True when the rule fired within its cooldown window."""
        last = (
            await session.execute(
                select(func.max(models.AlertEvent.ts)).where(
                    models.AlertEvent.rule_id == rule.id
                )
            )
        ).scalar_one_or_none()
        if last is None:
            return False
        last_aware = last if last.tzinfo else last.replace(tzinfo=UTC)
        return (now - last_aware).total_seconds() < rule.cooldown_seconds

    async def _dispatch(self, rule: models.AlertRule, finding: AlertFinding) -> bool:
        """Send the finding to the rule's channels; True if any delivered."""
        delivered = False
        for channel in _channels(rule):
            notifier = self._notifiers.get(channel)
            if notifier is None or not notifier.is_configured():
                continue
            if await notifier.send(finding.title, finding.body, finding.severity):
                delivered = True
        if not delivered:
            logger.info("alert '{}' fired but no channel delivered", rule.name)
        return delivered


def _channels(rule: models.AlertRule) -> list[str]:
    """Return the channel names a rule targets."""
    value: Any = rule.channels
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _resolve_zone(name: str) -> tzinfo:
    """Return the IANA zone, falling back to UTC on an unknown name."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone '{}', evaluating quiet hours in UTC", name)
        return UTC


def _in_quiet_hours(rule: models.AlertRule, now: datetime, zone: tzinfo = UTC) -> bool:
    """True when ``now`` falls within the rule's quiet-hours window.

    Hours are compared in ``zone`` (the user's timezone) so "no alerts
    22:00-07:00" means the user's night rather than UTC's.
    """
    quiet = rule.quiet_hours
    if not isinstance(quiet, dict):
        return False
    start = quiet.get("start_hour")
    end = quiet.get("end_hour")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    hour = now.astimezone(zone).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight
