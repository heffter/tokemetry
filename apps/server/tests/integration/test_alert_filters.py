"""Alert-rule dimension filter tests (Task 68.1).

Covers config parsing, the shared ledger-filter helper applied by the
ledger-scanning evaluators (burn_rate, unknown_model) across provider, model,
project, environment, and source, the provider-only scoping of limit_pct, the
content-free scoped-dimensions record, unchanged behavior for rules with no
filters, and API validation of the filters config.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import make_v1_event
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType
from tokemetry_server.db import models
from tokemetry_server.services.alerting.filters import filters_from_config
from tokemetry_server.services.alerting.rules import evaluate_rule
from tokemetry_server.services.sources import SourceRegistryService

_NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
_RECENT = _NOW - timedelta(minutes=10)


def _rule(kind: str, **kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {"name": kind, "kind": kind, "cooldown_seconds": 0, "config": {}}
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


def _seed(
    session: AsyncSession, event_id: str, *, provider: str = "anthropic", **fields: object
) -> None:
    """Add a recent final-attempt ledger row (defaults: anthropic, model 'm')."""
    session.add(
        make_v1_event(provider=provider, event_id=event_id, model="m", ts=_RECENT, **fields)
    )


# --------------------------------------------------------------------------- #
# Config parsing
# --------------------------------------------------------------------------- #

def test_filters_from_config_parses_lists() -> None:
    filters = filters_from_config(
        {"filters": {"provider": ["anthropic"], "project": ["p1", "p2"]}}
    )
    assert filters.provider == ("anthropic",)
    assert filters.project == ("p1", "p2")
    assert filters.scoped_dimensions() == ["provider", "project"]


def test_absent_or_empty_filters_are_unscoped() -> None:
    assert filters_from_config(None).is_empty
    assert filters_from_config({}).is_empty
    assert filters_from_config({"filters": {}}).is_empty
    assert filters_from_config({"filters": {"provider": []}}).is_empty


# --------------------------------------------------------------------------- #
# Ledger evaluators honor every dimension
# --------------------------------------------------------------------------- #

async def test_burn_rate_provider_filter_scopes_the_window(
    async_session: AsyncSession,
) -> None:
    _seed(async_session, "a", input_tokens=600_000)
    _seed(async_session, "b", provider="openai", input_tokens=600_000)
    await async_session.commit()

    # Unfiltered: (600k + 600k) / 60 = 20000/min, above the 15000 threshold.
    unfiltered = await evaluate_rule(
        async_session, _rule("burn_rate", warn_threshold=Decimal("15000")), _NOW
    )
    assert unfiltered is not None
    assert unfiltered.context["scoped_dimensions"] == []

    # Scoped to anthropic: 600k / 60 = 10000/min, below the threshold -> no fire.
    scoped = await evaluate_rule(
        async_session,
        _rule(
            "burn_rate",
            warn_threshold=Decimal("15000"),
            config={"filters": {"provider": ["anthropic"]}},
        ),
        _NOW,
    )
    assert scoped is None


async def test_burn_rate_records_scoped_dimensions_when_firing(
    async_session: AsyncSession,
) -> None:
    _seed(async_session, "a", input_tokens=600_000)
    await async_session.commit()
    finding = await evaluate_rule(
        async_session,
        _rule(
            "burn_rate",
            warn_threshold=Decimal("5000"),
            config={"filters": {"provider": ["anthropic"]}},
        ),
        _NOW,
    )
    assert finding is not None  # 10000/min >= 5000
    assert finding.context["scoped_dimensions"] == ["provider"]


async def test_unknown_model_filters_by_environment(
    async_session: AsyncSession,
) -> None:
    _seed(async_session, "e1", environment="prod")
    _seed(async_session, "e2", environment="staging")
    await async_session.commit()
    finding = await evaluate_rule(
        async_session,
        _rule("unknown_model", config={"filters": {"environment": ["prod"]}}),
        _NOW,
    )
    assert finding is not None
    assert finding.context["unpriced_events"] == 1
    assert finding.context["scoped_dimensions"] == ["environment"]


async def test_unknown_model_filters_by_source(async_session: AsyncSession) -> None:
    registry = SourceRegistryService(async_session)
    src_a = await registry.resolve_or_create(
        SourceRef(type=SourceType.GATEWAY, name="proxy-a", version="1"), _RECENT
    )
    src_b = await registry.resolve_or_create(
        SourceRef(type=SourceType.GATEWAY, name="proxy-b", version="1"), _RECENT
    )
    _seed(async_session, "s1", source_id=src_a)
    _seed(async_session, "s2", source_id=src_b)
    await async_session.commit()
    finding = await evaluate_rule(
        async_session,
        _rule("unknown_model", config={"filters": {"source": ["proxy-a"]}}),
        _NOW,
    )
    assert finding is not None
    assert finding.context["unpriced_events"] == 1


async def test_unfiltered_unknown_model_counts_all(async_session: AsyncSession) -> None:
    _seed(async_session, "u1")
    _seed(async_session, "u2", provider="openai")
    await async_session.commit()
    finding = await evaluate_rule(async_session, _rule("unknown_model"), _NOW)
    assert finding is not None
    assert finding.context["unpriced_events"] == 2
    assert finding.context["scoped_dimensions"] == []


# --------------------------------------------------------------------------- #
# Snapshot evaluator honors provider only
# --------------------------------------------------------------------------- #

async def test_limit_pct_provider_filter(async_session: AsyncSession) -> None:
    for provider in ("anthropic", "openai"):
        async_session.add(
            models.LimitSnapshot(
                provider=provider,
                ts=_NOW,
                window_kind="five_hour",
                utilization_pct=90.0,
                resets_at=None,
                provenance="official",
            )
        )
    await async_session.commit()

    # A provider with no snapshot never fires.
    absent = await evaluate_rule(
        async_session,
        _rule(
            "limit_pct",
            window_kind="five_hour",
            threshold=Decimal("80"),
            config={"filters": {"provider": ["zai"]}},
        ),
        _NOW,
    )
    assert absent is None

    scoped = await evaluate_rule(
        async_session,
        _rule(
            "limit_pct",
            window_kind="five_hour",
            threshold=Decimal("80"),
            config={"filters": {"provider": ["anthropic"]}},
        ),
        _NOW,
    )
    assert scoped is not None
    assert scoped.context["scoped_dimensions"] == ["provider"]


# --------------------------------------------------------------------------- #
# API validation
# --------------------------------------------------------------------------- #

def test_api_persists_filters(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/alerts",
        json={
            "name": "scoped",
            "kind": "burn_rate",
            "config": {"filters": {"provider": ["anthropic"]}},
        },
        headers=auth,
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["filters"]["provider"] == ["anthropic"]


def test_api_rejects_unknown_filter_key(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/alerts",
        json={"name": "bad-filter", "kind": "burn_rate", "config": {"filters": {"bogus": ["x"]}}},
        headers=auth,
    )
    assert response.status_code == 422


def test_api_rejects_unknown_config_key(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/alerts",
        json={"name": "bad-config", "kind": "burn_rate", "config": {"nope": 1}},
        headers=auth,
    )
    assert response.status_code == 422
