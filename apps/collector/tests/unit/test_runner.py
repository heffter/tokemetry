"""Unit tests for the collector runner (tail, queue, drain, bootstrap)."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.runner import Collector
from tokemetry_collector.state import CollectorState
from tokemetry_collector.uploader import Uploader
from tokemetry_core.providers.fake import FakeLimitsSource, FakeUsageSource


class _Server:
    """A controllable fake ingest server backing an httpx MockTransport."""

    def __init__(self) -> None:
        self.up = True
        self.received: list[tuple[str, dict[str, object]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("down")
        kind = request.url.path.rsplit("/", 1)[-1]
        import json

        self.received.append((kind, json.loads(request.content)))
        return httpx.Response(200, json={"accepted": 1})


@pytest.fixture
def config(tmp_path: Path) -> CollectorConfig:
    return CollectorConfig(
        server_url="http://server",
        api_token="tkm_token",
        machine_name="box-1",
        state_db_path=tmp_path / "state.sqlite3",
        upload_batch_size=500,
    )


@pytest.fixture
def state(config: CollectorConfig) -> Iterator[CollectorState]:
    with CollectorState(config.state_db_path) as store:
        yield store


@pytest.fixture
def server() -> _Server:
    return _Server()


def _uploader(server: _Server) -> Uploader:
    client = httpx.Client(transport=httpx.MockTransport(server.handler))
    return Uploader("http://server", "tkm_token", client=client)


def test_tail_enqueues_uploads_and_advances_offset(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    source = FakeUsageSource(files=1, events_per_file=2)
    collector = Collector(config, state, _uploader(server), [source])

    stats = collector.collect_once()

    assert stats.events_found == 2
    assert stats.batches_uploaded == 1
    assert state.queue_size() == 0
    assert server.received[0][0] == "events"
    uploaded_events = server.received[0][1]["events"]
    assert isinstance(uploaded_events, list)
    assert len(uploaded_events) == 2
    # Second cycle finds nothing new (offset at end of file).
    second = collector.collect_once()
    assert second.events_found == 0


def test_offline_keeps_batch_queued(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    server.up = False
    collector = Collector(config, state, _uploader(server), [FakeUsageSource()])

    stats = collector.collect_once()

    assert stats.batches_failed == 1
    assert stats.batches_uploaded == 0
    assert state.queue_size() == 1
    # When the server returns, the queued batch uploads without re-tailing.
    server.up = True
    recovered = collector.collect_once()
    assert recovered.batches_uploaded == 1
    assert state.queue_size() == 0


def test_dry_run_changes_nothing(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    collector = Collector(config, state, _uploader(server), [FakeUsageSource()], dry_run=True)

    stats = collector.collect_once()

    assert stats.events_found == 2
    assert stats.batches_enqueued == 0
    assert state.queue_size() == 0
    assert server.received == []
    assert state.get_offset("fake:FakeUsageSource", "fake-source-0.log") is None


def test_truncated_file_reparsed(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    source = FakeUsageSource(files=1, events_per_file=2)
    collector = Collector(config, state, _uploader(server), [source])
    collector.collect_once()
    # Simulate rotation: stored offset is now beyond a shrunken file.
    state.set_offset("fake:FakeUsageSource", "fake-source-0.log", 999_999, 999_999)

    stats = collector.collect_once()

    assert stats.events_found == 2  # re-read from the start


def test_batch_chunking(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    config.upload_batch_size = 1
    source = FakeUsageSource(files=1, events_per_file=3)
    collector = Collector(config, state, _uploader(server), [source])

    stats = collector.collect_once()

    assert stats.batches_uploaded == 3  # one event per batch


def test_limits_polled_and_uploaded(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    collector = Collector(config, state, _uploader(server), [], [FakeLimitsSource()])

    stats = collector.collect_once()

    assert stats.limits_found == 2
    kinds = [kind for kind, _ in server.received]
    assert "limits" in kinds


def test_unavailable_limits_degrade(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    collector = Collector(config, state, _uploader(server), [], [FakeLimitsSource(fail=True)])

    stats = collector.collect_once()

    assert stats.limits_found == 0
    assert server.received == []


def test_bootstrap_runs_once(
    config: CollectorConfig, state: CollectorState, server: _Server
) -> None:
    collector = Collector(config, state, _uploader(server), [FakeUsageSource()])

    first = collector.run_bootstrap()
    second = collector.run_bootstrap()

    assert first == 1
    assert second == 0
    assert state.get_meta("bootstrap_done") == "1"
