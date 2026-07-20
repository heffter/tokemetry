"""Proxy replay harness: schema conformance, ingest counts, replay safety.

Drives the fixture batches (:mod:`fixtures`) through the real ingest surface
via the Python client and asserts that (a) every fixture event conforms to the
published usage-event schema, (b) each batch produces its expected
accepted/updated/duplicate/rejected counts and row footprint, and (c) replaying
a batch is a pure no-op (AC-003) -- Task 65.4.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from tokemetry_core.usage_v2 import UsageEventV2, usage_event_json_schema

from .driver import ReplayDriver, post_batch
from .fixtures import ALL_SCENARIOS, Scenario, all_events, as_json

_SCENARIO_IDS = [scenario.name for scenario in ALL_SCENARIOS]


def _row_count(engine: sa.Engine, table: str = "usage_events_v2") -> int:
    with engine.connect() as conn:
        return int(conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one())


def test_every_fixture_event_conforms_to_published_schema() -> None:
    """Each fixture event validates and uses only published schema properties."""
    allowed = set(usage_event_json_schema()["properties"])
    for event in all_events():
        # Round-trips through the same model the ingest route enforces.
        UsageEventV2.model_validate(event)
        unknown = set(event) - allowed
        assert not unknown, f"{event['event_id']} has non-schema keys: {unknown}"


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_SCENARIO_IDS)
def test_scenario_ingests_with_expected_counts(
    scenario: Scenario,
    client: TestClient,
    ingest_token: str,
    read_engine: sa.Engine,
) -> None:
    """Each batch produces its documented count breakdown and row footprint."""
    body = post_batch(client, ingest_token, scenario.events)
    assert body["accepted"] == scenario.accepted, scenario.name
    assert body["updated"] == scenario.updated, scenario.name
    assert body["duplicate"] == scenario.duplicate, scenario.name
    assert body["rejected"] == scenario.rejected, scenario.name
    assert _row_count(read_engine) == scenario.rows, scenario.name


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_SCENARIO_IDS)
def test_scenario_replay_through_client_is_noop(
    scenario: Scenario,
    driver: ReplayDriver,
    read_engine: sa.Engine,
) -> None:
    """Replaying a batch via the Python client accepts nothing new (AC-003)."""
    first = driver.replay(scenario.events)
    assert first.accepted == scenario.accepted, scenario.name
    assert first.rejected == 0, scenario.name  # no client-side poison isolation
    rows_after_first = _row_count(read_engine)
    assert rows_after_first == scenario.rows, scenario.name

    second = driver.replay(scenario.events)
    assert second.accepted == 0, f"{scenario.name} replay accepted new events"
    assert _row_count(read_engine) == rows_after_first, scenario.name


def test_full_replay_of_all_scenarios_is_idempotent(
    client: TestClient,
    ingest_token: str,
    driver: ReplayDriver,
    read_engine: sa.Engine,
) -> None:
    """The whole fixture corpus ingests once, then a full replay adds no rows."""
    events = all_events()
    first = driver.replay(events)
    total_rows = _row_count(read_engine)
    assert first.accepted == sum(s.accepted for s in ALL_SCENARIOS)
    assert total_rows == sum(s.rows for s in ALL_SCENARIOS)

    second = driver.replay(events)
    assert second.accepted == 0
    assert _row_count(read_engine) == total_rows


def test_fixtures_serialize_to_portable_json() -> None:
    """The shared-truth JSON export round-trips (consumed by the proxy repo)."""
    payload = json.loads(as_json())
    assert [entry["name"] for entry in payload] == _SCENARIO_IDS
    assert all(entry["events"] for entry in payload)
