"""Unit tests for the Claude Code JSONL usage source.

Fixtures mirror the real transcript schema observed on a live machine
(Claude Code 2.1.x), including the duplicate-requestId streaming behavior
that caused systematic undercounting in other tools.
"""

import json
from pathlib import Path
from typing import Any

from tokemetry_core.models import SourceFile
from tokemetry_core.providers.claude_code import ClaudeCodeJsonlSource


def _assistant_line(
    request_id: str = "req_011",
    output_tokens: int = 365,
    input_tokens: int = 10088,
    **overrides: Any,
) -> str:
    """Build a realistic assistant transcript line as JSON text."""
    record: dict[str, Any] = {
        "parentUuid": "aaa",
        "isSidechain": False,
        "type": "assistant",
        "uuid": "bbb",
        "timestamp": "2026-07-09T09:41:14.123Z",
        "sessionId": "183cec59-7562-4760-8a4c-4512784e7e46",
        "sessionKind": "bg",
        "userType": "external",
        "entrypoint": "cli",
        "cwd": "C:\\devel\\tokemetry",
        "version": "2.1.205",
        "gitBranch": "master",
        "slug": "some-session-slug",
        "requestId": request_id,
        "message": {
            "id": "msg_01",
            "type": "message",
            "role": "assistant",
            "model": "claude-fable-5",
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 8583,
                "cache_read_input_tokens": 25502,
                "output_tokens": output_tokens,
                "server_tool_use": {"web_search_requests": 2, "web_fetch_requests": 0},
                "service_tier": "standard",
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 8583,
                    "ephemeral_5m_input_tokens": 0,
                },
                "speed": "standard",
            },
        },
    }
    message_overrides = overrides.pop("message_overrides", None)
    record.update(overrides)
    if message_overrides:
        record["message"].update(message_overrides)
    return json.dumps(record)


def _write(path: Path, *lines: str, incomplete: str | None = None) -> SourceFile:
    """Write JSONL lines (plus an optional unterminated tail) to ``path``.

    Bytes are written directly so line endings stay ``\\n`` on every OS,
    matching the transcripts Claude Code produces.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(line + "\n" for line in lines)
    if incomplete is not None:
        payload += incomplete
    path.write_bytes(payload.encode("utf-8"))
    return SourceFile(path=path, size=path.stat().st_size)


class TestDiscover:
    """Transcript discovery, including subagent files."""

    def test_finds_session_and_subagent_transcripts(self, tmp_path: Path) -> None:
        home = tmp_path / ".claude"
        _write(home / "projects" / "C--devel" / "s1.jsonl", _assistant_line())
        _write(
            home / "projects" / "C--devel" / "s1" / "subagents" / "agent-1.jsonl",
            _assistant_line(request_id="req_sub"),
        )

        files = ClaudeCodeJsonlSource(claude_home=home).discover()

        names = [file.path.name for file in files]
        assert names == ["agent-1.jsonl", "s1.jsonl"]
        assert all(file.size > 0 for file in files)

    def test_missing_projects_dir_yields_nothing(self, tmp_path: Path) -> None:
        assert ClaudeCodeJsonlSource(claude_home=tmp_path).discover() == []


class TestParse:
    """Record normalization, dedup, and offset semantics."""

    def test_maps_all_fields(self, tmp_path: Path) -> None:
        file = _write(tmp_path / "s.jsonl", _assistant_line())
        source = ClaudeCodeJsonlSource(claude_home=tmp_path, machine="box-1")

        result = source.parse(file, offset=0)

        assert len(result.events) == 1
        event = result.events[0]
        assert event.event_id == "req_011"
        assert event.provider == "anthropic"
        assert event.native_model == "claude-fable-5"
        assert event.machine == "box-1"
        assert event.session_id == "183cec59-7562-4760-8a4c-4512784e7e46"
        assert event.project == "C:\\devel\\tokemetry"
        assert event.git_branch == "master"
        assert event.client_version == "2.1.205"
        assert event.entrypoint == "cli"
        assert event.session_kind == "bg"
        assert event.is_sidechain is False
        assert event.input_tokens == 10088
        assert event.output_tokens == 365
        assert event.cache_read_tokens == 25502
        assert event.cache_write_short_tokens == 0
        assert event.cache_write_long_tokens == 8583
        assert event.service_tier == "standard"
        assert event.speed == "standard"
        assert event.extra == {"web_search_requests": 2}
        assert event.ts.isoformat() == "2026-07-09T09:41:14.123000+00:00"

    def test_duplicate_request_ids_keep_max_output(self, tmp_path: Path) -> None:
        file = _write(
            tmp_path / "s.jsonl",
            _assistant_line(output_tokens=1, input_tokens=1),  # streaming placeholder
            _assistant_line(output_tokens=648),  # settled record
            _assistant_line(output_tokens=100),  # late partial snapshot
        )

        result = ClaudeCodeJsonlSource(claude_home=tmp_path).parse(file, offset=0)

        assert len(result.events) == 1
        assert result.events[0].output_tokens == 648
        assert result.events[0].input_tokens == 10088

    def test_distinct_request_ids_all_kept_in_order(self, tmp_path: Path) -> None:
        file = _write(
            tmp_path / "s.jsonl",
            _assistant_line(request_id="req_a"),
            _assistant_line(request_id="req_b"),
        )

        result = ClaudeCodeJsonlSource(claude_home=tmp_path).parse(file, offset=0)

        assert [event.event_id for event in result.events] == ["req_a", "req_b"]

    def test_ignores_non_assistant_and_synthetic_records(self, tmp_path: Path) -> None:
        user_line = json.dumps({"type": "user", "message": {"role": "user"}})
        synthetic = _assistant_line(
            request_id="req_syn", message_overrides={"model": "<synthetic>"}
        )
        file = _write(tmp_path / "s.jsonl", user_line, synthetic, _assistant_line())

        result = ClaudeCodeJsonlSource(claude_home=tmp_path).parse(file, offset=0)

        assert [event.event_id for event in result.events] == ["req_011"]
        assert result.malformed_lines == 0

    def test_malformed_lines_counted_not_fatal(self, tmp_path: Path) -> None:
        file = _write(tmp_path / "s.jsonl", "{not json", _assistant_line())

        result = ClaudeCodeJsonlSource(claude_home=tmp_path).parse(file, offset=0)

        assert len(result.events) == 1
        assert result.malformed_lines == 1

    def test_incomplete_trailing_line_not_consumed(self, tmp_path: Path) -> None:
        complete = _assistant_line(request_id="req_done")
        partial = _assistant_line(request_id="req_partial")
        file = _write(tmp_path / "s.jsonl", complete, incomplete=partial[:50])
        source = ClaudeCodeJsonlSource(claude_home=tmp_path)

        first = source.parse(file, offset=0)

        assert [event.event_id for event in first.events] == ["req_done"]
        assert first.new_offset == len(complete.encode()) + 1

        # The writer finishes the line; the next pass picks it up.
        with file.path.open("ab") as handle:
            handle.write((partial[50:] + "\n").encode("utf-8"))
        refreshed = SourceFile(path=file.path, size=file.path.stat().st_size)

        second = source.parse(refreshed, first.new_offset)

        assert [event.event_id for event in second.events] == ["req_partial"]

    def test_resume_from_offset_skips_old_events(self, tmp_path: Path) -> None:
        file = _write(tmp_path / "s.jsonl", _assistant_line(request_id="req_old"))
        source = ClaudeCodeJsonlSource(claude_home=tmp_path)
        first = source.parse(file, offset=0)

        with file.path.open("ab") as handle:
            handle.write((_assistant_line(request_id="req_new") + "\n").encode("utf-8"))
        refreshed = SourceFile(path=file.path, size=file.path.stat().st_size)

        second = source.parse(refreshed, first.new_offset)

        assert [event.event_id for event in second.events] == ["req_new"]

    def test_legacy_cache_field_without_ttl_breakdown(self, tmp_path: Path) -> None:
        line = _assistant_line(
            message_overrides={
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 7,
                    "cache_creation_input_tokens": 900,
                    "cache_read_input_tokens": 100,
                }
            }
        )
        file = _write(tmp_path / "s.jsonl", line)

        result = ClaudeCodeJsonlSource(claude_home=tmp_path).parse(file, offset=0)

        event = result.events[0]
        assert event.cache_write_short_tokens == 900
        assert event.cache_write_long_tokens == 0
        assert event.cache_read_tokens == 100


class TestBootstrap:
    """stats-cache.json import."""

    def test_imports_daily_model_tokens(self, tmp_path: Path) -> None:
        cache = {
            "version": 4,
            "dailyModelTokens": [
                {
                    "date": "2026-06-20",
                    "tokensByModel": {"claude-fable-5": 123456, "claude-opus-4-8": 999},
                },
                {"date": "not-a-date", "tokensByModel": {"claude-fable-5": 1}},
                "garbage",
            ],
        }
        (tmp_path / "stats-cache.json").write_text(json.dumps(cache), encoding="utf-8")

        aggregates = ClaudeCodeJsonlSource(claude_home=tmp_path, machine="box-1").bootstrap()

        assert len(aggregates) == 2
        by_model = {aggregate.native_model: aggregate for aggregate in aggregates}
        assert by_model["claude-fable-5"].total_tokens == 123456
        assert by_model["claude-fable-5"].day.isoformat() == "2026-06-20"
        assert by_model["claude-fable-5"].machine == "box-1"

    def test_missing_cache_returns_empty(self, tmp_path: Path) -> None:
        assert ClaudeCodeJsonlSource(claude_home=tmp_path).bootstrap() == []

    def test_corrupt_cache_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "stats-cache.json").write_text("{broken", encoding="utf-8")
        assert ClaudeCodeJsonlSource(claude_home=tmp_path).bootstrap() == []
