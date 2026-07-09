"""End-to-end collector test with the real Claude Code source.

Builds a temporary ``~/.claude`` with a transcript and a stats cache, runs
the collector against a fake ingest server (httpx MockTransport), and asserts
that events and bootstrap aggregates are serialized and uploaded correctly.
"""

import json
from pathlib import Path
from typing import Any

import httpx
from tokemetry_collector.config import CollectorConfig
from tokemetry_collector.runner import Collector
from tokemetry_collector.sources import build_usage_sources
from tokemetry_collector.state import CollectorState
from tokemetry_collector.uploader import Uploader


def _assistant_line(request_id: str, output_tokens: int) -> str:
    record: dict[str, Any] = {
        "type": "assistant",
        "uuid": "u",
        "timestamp": "2026-07-09T09:41:14+00:00",
        "sessionId": "sess-1",
        "cwd": "C:/proj",
        "version": "2.1.205",
        "gitBranch": "main",
        "requestId": request_id,
        "message": {
            "id": "msg",
            "model": "claude-opus-4-5",
            "usage": {
                "input_tokens": 10,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 500,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 800,
                },
            },
        },
    }
    return json.dumps(record)


def _make_claude_home(tmp_path: Path) -> Path:
    home = tmp_path / ".claude"
    transcript = home / "projects" / "C--proj" / "sess-1.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_bytes(
        (_assistant_line("req_1", 100) + "\n" + _assistant_line("req_2", 200) + "\n").encode()
    )
    stats = {
        "version": 4,
        "dailyModelTokens": [
            {"date": "2026-06-01", "tokensByModel": {"claude-opus-4-5": 5000}}
        ],
    }
    (home / "stats-cache.json").write_text(json.dumps(stats), encoding="utf-8")
    return home


class _Server:
    def __init__(self) -> None:
        self.received: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        kind = request.url.path.rsplit("/", 1)[-1]
        self.received.append((kind, json.loads(request.content)))
        return httpx.Response(200, json={"accepted": 1})


def _config(tmp_path: Path, home: Path) -> CollectorConfig:
    return CollectorConfig.model_validate(
        {
            "server_url": "http://server",
            "api_token": "tkm_token",
            "machine_name": "box-1",
            "state_db_path": str(tmp_path / "state.sqlite3"),
            "sources": {"claude_code": {"enabled": True, "claude_home": str(home)}},
        }
    )


def test_collects_and_uploads_events(tmp_path: Path) -> None:
    home = _make_claude_home(tmp_path)
    config = _config(tmp_path, home)
    server = _Server()
    client = httpx.Client(transport=httpx.MockTransport(server.handler))

    with CollectorState(config.state_db_path) as state:
        collector = Collector(
            config, state, Uploader("http://server", "tkm_token", client=client),
            build_usage_sources(config),
        )
        stats = collector.collect_once(poll_limits=False)

    assert stats.events_found == 2
    assert stats.batches_uploaded == 1
    events_batch = next(payload for kind, payload in server.received if kind == "events")
    assert events_batch["machine"]["name"] == "box-1"
    models = {event["native_model"] for event in events_batch["events"]}
    assert models == {"claude-opus-4-5"}


def test_bootstrap_uploads_aggregates(tmp_path: Path) -> None:
    home = _make_claude_home(tmp_path)
    config = _config(tmp_path, home)
    server = _Server()
    client = httpx.Client(transport=httpx.MockTransport(server.handler))

    with CollectorState(config.state_db_path) as state:
        collector = Collector(
            config, state, Uploader("http://server", "tkm_token", client=client),
            build_usage_sources(config),
        )
        enqueued = collector.run_bootstrap()
        collector.collect_once(poll_limits=False)

    assert enqueued == 1
    bootstrap_batch = next(payload for kind, payload in server.received if kind == "bootstrap")
    aggregates = bootstrap_batch["aggregates"]
    assert aggregates[0]["total_tokens"] == 5000
    assert aggregates[0]["day"] == "2026-06-01"


def test_build_usage_sources_registers_claude_code(tmp_path: Path) -> None:
    home = _make_claude_home(tmp_path)
    config = _config(tmp_path, home)
    sources = build_usage_sources(config)
    assert len(sources) == 1
    assert sources[0].provider == "anthropic"
