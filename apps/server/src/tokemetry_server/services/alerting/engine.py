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
from tokemetry_server.services.alerting.rules import AlertFinding, evaluate_rule

#: Firing-state values stored on ``AlertRule.state``.
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

    def __init__(self, notifiers: dict[str, Notifier], timezone: str = "UTC") -> None:
        """Create the engine over a channel-name -> notifier registry.

        ``timezone`` is the IANA name in which quiet hours are evaluated; an
        unknown name falls back to UTC.
        """
        self._notifiers = notifiers
        self._tz = _resolve_zone(timezone)

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
