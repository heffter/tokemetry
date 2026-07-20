"""Cost-reconciliation drift and source-freshness tests (Task 65.6).

Covers the reconciliation query (per provider and per provider-and-day, with
percentage drift), the HTTP endpoint and its scope enforcement, the end-to-end
flow of a proxy-reported ``observed_cost`` from ingest through pricing into the
reconciliation surface, and gateway source-freshness transitions under an
injected clock (a silent exporter goes stale past its threshold).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from conftest import make_v1_event
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.usage_v2 import SourceRef, SourceType
from tokemetry_server.db import models
from tokemetry_server.scopes import INGEST_EVENTS
from tokemetry_server.services.computed_costs import record_cost
from tokemetry_server.services.queries_v2 import cost_reconciliation
from tokemetry_server.services.query_framework import QueryFilters
from tokemetry_server.services.sources import SourceHealthService, SourceRegistryService

_TS = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)
_START = datetime(2026, 7, 1, tzinfo=UTC)
_END = datetime(2026, 8, 1, tzinfo=UTC)
_NONE = QueryFilters()
_RANGE = {"from": "2026-07-01T00:00:00Z", "to": "2026-08-01T00:00:00Z"}


async def _seed_cost(
    session: AsyncSession,
    event_id: str,
    *,
    amount: str,
    observed: str | None,
    ts: datetime = _TS,
    provider: str = "anthropic",
) -> None:
    """Seed one final attempt plus its active computed cost (with observed)."""
    session.add(
        make_v1_event(
            provider=provider,
            event_id=event_id,
            model="claude-sonnet-4-5",
            ts=ts,
            input_tokens=1000,
        )
    )
    await session.flush()
    await record_cost(
        session,
        provider,
        event_id,
        amount=Decimal(amount),
        cost_status="priced",
        pricing_version="1",
        observed_cost=Decimal(observed) if observed is not None else None,
    )


# --------------------------------------------------------------------------- #
# Reconciliation query (service layer)
# --------------------------------------------------------------------------- #

async def test_agreeing_costs_have_zero_drift(async_session: AsyncSession) -> None:
    await _seed_cost(async_session, "a:1", amount="0.005", observed="0.005")
    await async_session.commit()

    (row,) = await cost_reconciliation(async_session, _START, _END, _NONE)
    assert row.provider == "anthropic"
    assert row.computed_usd == Decimal("0.005")
    assert row.observed_usd == Decimal("0.005")
    assert row.drift_usd == Decimal("0")
    assert row.drift_pct == Decimal("0.00")
    assert row.day is None


async def test_drift_and_percentage_are_reported(async_session: AsyncSession) -> None:
    await _seed_cost(async_session, "a:1", amount="0.005", observed="0.006")
    await async_session.commit()

    (row,) = await cost_reconciliation(async_session, _START, _END, _NONE)
    assert row.computed_usd == Decimal("0.005")
    assert row.observed_usd == Decimal("0.006")
    assert row.drift_usd == Decimal("0.001")
    # (0.006 - 0.005) / 0.005 * 100 == 20 percent.
    assert row.drift_pct == Decimal("20.00")


async def test_events_without_observed_cost_are_excluded(
    async_session: AsyncSession,
) -> None:
    await _seed_cost(async_session, "a:1", amount="0.005", observed="0.006")
    await _seed_cost(async_session, "a:2", amount="0.009", observed=None)
    await async_session.commit()

    (row,) = await cost_reconciliation(async_session, _START, _END, _NONE)
    # Only the event carrying an observed cost participates.
    assert row.computed_usd == Decimal("0.005")
    assert row.observed_usd == Decimal("0.006")


async def test_group_by_day_splits_per_provider_and_day(
    async_session: AsyncSession,
) -> None:
    await _seed_cost(async_session, "a:1", amount="0.005", observed="0.006", ts=_TS)
    await _seed_cost(
        async_session, "a:2", amount="0.010", observed="0.010",
        ts=_TS + timedelta(days=1),
    )
    await async_session.commit()

    rows = await cost_reconciliation(async_session, _START, _END, _NONE, group_by="day")
    assert [r.day for r in rows] == ["2026-07-10", "2026-07-11"]
    assert rows[0].drift_usd == Decimal("0.001")
    assert rows[1].drift_usd == Decimal("0")


async def test_zero_computed_cost_yields_null_percentage(
    async_session: AsyncSession,
) -> None:
    await _seed_cost(async_session, "a:1", amount="0", observed="0.004")
    await async_session.commit()

    (row,) = await cost_reconciliation(async_session, _START, _END, _NONE)
    assert row.drift_usd == Decimal("0.004")
    assert row.drift_pct is None  # no percentage against a zero base


# --------------------------------------------------------------------------- #
# Reconciliation HTTP endpoint + scope enforcement
# --------------------------------------------------------------------------- #

def test_reconciliation_endpoint_rejects_unknown_group_by(
    client: TestClient, auth: dict[str, str]
) -> None:
    response = client.get(
        "/api/v2/costs/reconciliation",
        params={**_RANGE, "group_by": "machine"},
        headers=auth,
    )
    assert response.status_code == 400


def test_reconciliation_requires_query_read_scope(
    client: TestClient, auth: dict[str, str]
) -> None:
    token = client.post(
        "/api/v1/tokens",
        json={"label": "ingest-only", "scopes": [INGEST_EVENTS]},
        headers=auth,
    ).json()["token"]
    forbidden = client.get(
        "/api/v2/costs/reconciliation",
        params=_RANGE,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert forbidden.status_code == 403


def _wire_event(event_id: str, **over: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "attempt",
        "finality": "final",
        "sequence": 1,
        "provider": "anthropic",
        "native_model": "claude-sonnet-4-5",
        "ts_started": "2026-07-10T12:00:00Z",
        "input_tokens": 1000,
        "success": True,
        "outcome": "success",
        "source": {"type": "gateway", "name": "aiProviderProxy", "version": "1.0.0"},
    }
    event.update(over)
    return event


def test_observed_cost_flows_from_ingest_to_reconciliation(
    client: TestClient, auth: dict[str, str]
) -> None:
    """A proxy-reported observed cost survives ingest, pricing, and the query."""
    ingest = client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": [_wire_event("recon:1", observed_cost="0.012")]},
        headers=auth,
    )
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["accepted"] == 1

    # Price the input tokens so the computed cost is non-zero, then reprice.
    rate = client.post(
        "/api/v2/pricing",
        json={
            "provider": "anthropic",
            "native_model": "claude-sonnet-4-5",
            "unit_type": "input_token",
            "effective_from": "2026-01-01",
            "unit_price": "0.000005",
        },
        headers=auth,
    )
    assert rate.status_code == 201, rate.text
    reprice = client.post(
        "/api/v2/pricing/reprice",
        json={"start": _RANGE["from"], "end": _RANGE["to"]},
        headers=auth,
    )
    assert reprice.status_code == 200, reprice.text

    response = client.get("/api/v2/costs/reconciliation", params=_RANGE, headers=auth)
    assert response.status_code == 200, response.text
    (row,) = response.json()["rows"]
    assert row["provider"] == "anthropic"
    assert Decimal(row["observed_usd"]) == Decimal("0.012")  # the proxy's value
    assert Decimal(row["computed_usd"]) == Decimal("0.005")  # 1000 * 0.000005
    assert Decimal(row["drift_usd"]) == Decimal("0.007")
    assert row["drift_pct"] is not None


# --------------------------------------------------------------------------- #
# Source freshness (silent-exporter transition)
# --------------------------------------------------------------------------- #

async def test_silent_gateway_exporter_goes_stale_after_threshold(
    async_session: AsyncSession,
) -> None:
    """A gateway that stops exporting is fresh within, stale past, its window."""
    source_id = await SourceRegistryService(async_session).resolve_or_create(
        SourceRef(type=SourceType.GATEWAY, name="aiProviderProxy", version="1.0.0"),
        _TS,
    )
    health = SourceHealthService(async_session)
    await health.record_ingest(source_id, _TS, schema_version=2, max_event_ts=_TS, error_count=0)
    await async_session.commit()
    source = await async_session.get(models.Source, source_id)
    assert source is not None

    # The gateway staleness window is 10 minutes.
    assert health.staleness_threshold("gateway") == 600.0
    assert health.health(source, now=_TS + timedelta(minutes=5)).stale is False
    assert health.health(source, now=_TS + timedelta(minutes=15)).stale is True
