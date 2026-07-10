"""Alert rule CRUD, history, and manual evaluation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokemetry_server.api.auth import require_token
from tokemetry_server.api.deps import get_session
from tokemetry_server.api.schemas_alerts import (
    AlertEventOut,
    AlertRuleIn,
    AlertRuleOut,
    EvaluateResult,
    TestChannelResult,
)
from tokemetry_server.db import models
from tokemetry_server.services.alerting.rules import EVALUATORS

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


def _rule_out(rule: models.AlertRule) -> AlertRuleOut:
    """Map an alert rule ORM row to its response schema."""
    channels: list[Any] = rule.channels if isinstance(rule.channels, list) else []
    return AlertRuleOut(
        id=rule.id,
        name=rule.name,
        kind=rule.kind,
        threshold=rule.threshold,
        warn_threshold=rule.warn_threshold,
        crit_threshold=rule.crit_threshold,
        window_kind=rule.window_kind,
        channels=[str(c) for c in channels],
        cooldown_seconds=rule.cooldown_seconds,
        quiet_hours=rule.quiet_hours,
        enabled=rule.enabled,
        config=rule.config,
        state=rule.state,
        last_fired_at=rule.last_fired_at,
    )


def _validate_kind(kind: str) -> None:
    """Reject rules whose kind has no evaluator."""
    if kind not in EVALUATORS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown rule kind; valid: {sorted(EVALUATORS)}",
        )


@router.get("", response_model=list[AlertRuleOut])
async def list_rules(
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[AlertRuleOut]:
    """List all alert rules."""
    result = await session.execute(select(models.AlertRule).order_by(models.AlertRule.name))
    return [_rule_out(rule) for rule in result.scalars()]


@router.post("", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    payload: AlertRuleIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> AlertRuleOut:
    """Create an alert rule."""
    _validate_kind(payload.kind)
    existing = await session.execute(
        select(models.AlertRule).where(models.AlertRule.name == payload.name)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="name exists")
    rule = models.AlertRule(
        name=payload.name,
        kind=payload.kind,
        threshold=payload.threshold,
        warn_threshold=payload.warn_threshold,
        crit_threshold=payload.crit_threshold,
        window_kind=payload.window_kind,
        channels=payload.channels,
        cooldown_seconds=payload.cooldown_seconds,
        quiet_hours=payload.quiet_hours,
        enabled=payload.enabled,
        config=payload.config,
    )
    session.add(rule)
    await session.flush()
    return _rule_out(rule)


@router.put("/{rule_id}", response_model=AlertRuleOut)
async def update_rule(
    rule_id: int,
    payload: AlertRuleIn,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> AlertRuleOut:
    """Replace an alert rule's fields."""
    _validate_kind(payload.kind)
    rule = await session.get(models.AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown rule")
    rule.name = payload.name
    rule.kind = payload.kind
    rule.threshold = payload.threshold
    rule.warn_threshold = payload.warn_threshold
    rule.crit_threshold = payload.crit_threshold
    rule.window_kind = payload.window_kind
    rule.channels = payload.channels
    rule.cooldown_seconds = payload.cooldown_seconds
    rule.quiet_hours = payload.quiet_hours
    rule.enabled = payload.enabled
    rule.config = payload.config
    return _rule_out(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> Response:
    """Delete an alert rule and its events."""
    rule = await session.get(models.AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown rule")
    await session.delete(rule)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/events", response_model=list[AlertEventOut])
async def list_events(
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> list[AlertEventOut]:
    """Return recent alert events, newest first."""
    result = await session.execute(
        select(models.AlertEvent).order_by(models.AlertEvent.ts.desc()).limit(limit)
    )
    return [
        AlertEventOut(
            id=event.id,
            rule_id=event.rule_id,
            ts=event.ts if event.ts.tzinfo else event.ts.replace(tzinfo=UTC),
            severity=event.severity,
            title=event.title,
            body=event.body,
            delivered=event.delivered,
            context=event.context,
        )
        for event in result.scalars()
    ]


@router.post("/test/{channel}", response_model=TestChannelResult)
async def test_channel(
    channel: str,
    request: Request,
    _: str = Depends(require_token),
) -> TestChannelResult:
    """Send a test notification through one channel and report the outcome."""
    engine = request.app.state.alert_engine
    delivered = await engine.test_channel(channel)
    return TestChannelResult(channel=channel, delivered=delivered)


@router.post("/evaluate", response_model=EvaluateResult)
async def evaluate_now(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_token),
) -> EvaluateResult:
    """Run the alert engine immediately and return what fired."""
    engine = request.app.state.alert_engine
    fired = await engine.run(session)
    await session.flush()
    now = datetime.now(UTC)
    return EvaluateResult(
        fired=[
            AlertEventOut(
                id=0,
                rule_id=0,
                ts=now,
                severity=item.finding.severity,
                title=item.finding.title,
                body=item.finding.body,
                delivered=item.delivered,
                context=item.finding.context,
            )
            for item in fired
        ]
    )
