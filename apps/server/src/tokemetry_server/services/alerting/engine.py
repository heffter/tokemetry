"""Alert engine: evaluate enabled rules and dispatch notifications.

For each enabled rule the engine honors quiet hours and a per-rule cooldown,
evaluates the condition, dispatches any finding to the rule's channels, and
records an ``alert_events`` row (whether or not delivery succeeded, so the
history is complete).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.db import models
from tokemetry_server.services.alerting.notifiers import Notifier
from tokemetry_server.services.alerting.rules import AlertFinding, evaluate_rule


@dataclass(frozen=True)
class FiredAlert:
    """Result of a rule that fired during an evaluation pass."""

    rule_name: str
    finding: AlertFinding
    delivered: bool


class AlertEngine:
    """Evaluates alert rules and dispatches through notification channels."""

    def __init__(self, notifiers: dict[str, Notifier]) -> None:
        """Create the engine over a channel-name -> notifier registry."""
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
            if _in_quiet_hours(rule, reference):
                continue
            if await self._in_cooldown(session, rule, reference):
                continue
            finding = await evaluate_rule(session, rule, reference)
            if finding is None:
                continue
            delivered = await self._dispatch(rule, finding)
            session.add(
                models.AlertEvent(
                    rule_id=rule.id,
                    ts=reference,
                    severity=finding.severity,
                    title=finding.title,
                    body=finding.body,
                    delivered=delivered,
                    context=finding.context,
                )
            )
            fired.append(FiredAlert(rule.name, finding, delivered))
        return fired

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
            if await notifier.send(finding.title, finding.body):
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


def _in_quiet_hours(rule: models.AlertRule, now: datetime) -> bool:
    """True when ``now`` falls within the rule's UTC quiet-hours window."""
    quiet = rule.quiet_hours
    if not isinstance(quiet, dict):
        return False
    start = quiet.get("start_hour")
    end = quiet.get("end_hour")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    hour = now.astimezone(UTC).hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight
