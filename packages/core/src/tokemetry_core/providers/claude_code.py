"""Claude Code JSONL usage source.

Parses the transcripts Claude Code writes under
``<claude_home>/projects/<encoded-cwd>/<session>.jsonl`` (plus per-subagent
transcripts in ``<session>/subagents/``) and the aggregate
``stats-cache.json`` for history bootstrap.

Correctness notes, learned from ecosystem bugs:

- One logical API request can emit several JSONL lines sharing a
  ``requestId`` (streaming snapshots followed by the final record).
  Within a parse pass the entry with the largest output token count wins
  (last one on ties); the server's keep-max upsert resolves duplicates
  across passes. Keeping the first entry undercounts output up to 5x.
- ``input_tokens`` can be a streaming placeholder; it is taken as-is but
  the max-selection above prefers the settled record.
- A trailing line without a newline is an in-progress write: it is not
  consumed, and ``new_offset`` stays at its start so the next pass re-reads
  it complete.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tokemetry_core.interfaces import UsageSource
from tokemetry_core.models import (
    DailyAggregate,
    ParseResult,
    Provenance,
    SourceFile,
    UsageEvent,
)

ANTHROPIC_PROVIDER = "anthropic"

#: Model ids Claude Code writes for non-billable synthetic records.
_SYNTHETIC_MODELS = {"<synthetic>"}


def default_claude_home() -> Path:
    """Return the Claude Code data directory for this machine.

    Honors ``CLAUDE_CONFIG_DIR`` when set, otherwise ``~/.claude``.
    """
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".claude"


class ClaudeCodeJsonlSource(UsageSource):
    """Usage source over Claude Code transcript files."""

    provider = ANTHROPIC_PROVIDER

    def __init__(self, claude_home: Path | None = None, machine: str | None = None) -> None:
        """Create a source rooted at a Claude Code data directory.

        Args:
            claude_home: Directory containing ``projects/`` and
                ``stats-cache.json``; resolved via
                :func:`default_claude_home` when omitted.
            machine: Machine name stamped on every emitted event.
        """
        self._home = claude_home if claude_home is not None else default_claude_home()
        self._machine = machine

    def discover(self) -> list[SourceFile]:
        """Find all transcript files, including subagent transcripts."""
        projects = self._home / "projects"
        if not projects.is_dir():
            return []
        files = []
        for path in sorted(projects.rglob("*.jsonl")):
            try:
                size = path.stat().st_size
            except OSError:
                continue  # deleted between listing and stat
            files.append(SourceFile(path=path, size=size))
        return files

    def parse(self, file: SourceFile, offset: int) -> ParseResult:
        """Parse assistant usage records from ``file`` starting at ``offset``."""
        best: dict[str, UsageEvent] = {}
        order: list[str] = []
        malformed = 0

        with file.path.open("rb") as handle:
            handle.seek(offset)
            position = offset
            for raw_line in handle:
                if not raw_line.endswith(b"\n"):
                    break  # in-progress write; re-read complete next pass
                position += len(raw_line)
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    malformed += 1
                    continue
                try:
                    event = self._event_from_record(record)
                except (KeyError, TypeError, ValueError):
                    malformed += 1
                    continue
                if event is None:
                    continue
                current = best.get(event.event_id)
                if current is None:
                    order.append(event.event_id)
                    best[event.event_id] = event
                elif event.output_tokens >= current.output_tokens:
                    best[event.event_id] = event

        return ParseResult(
            events=tuple(best[event_id] for event_id in order),
            new_offset=position,
            malformed_lines=malformed,
        )

    def bootstrap(self) -> list[DailyAggregate]:
        """Import per-day, per-model totals from ``stats-cache.json``.

        The cache only stores a total token count per model per day (no
        input/output split), so aggregates carry ``total_tokens`` only.
        Missing or unreadable cache yields an empty list -- bootstrap is
        best effort by design.
        """
        cache_path = self._home / "stats-cache.json"
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

        aggregates = []
        for entry in data.get("dailyModelTokens", []):
            if not isinstance(entry, dict):
                continue
            try:
                day = datetime.strptime(str(entry["date"]), "%Y-%m-%d").replace(tzinfo=UTC).date()
            except (KeyError, ValueError):
                continue
            tokens_by_model = entry.get("tokensByModel")
            if not isinstance(tokens_by_model, dict):
                continue
            for model, total in tokens_by_model.items():
                if not isinstance(total, int) or total < 0:
                    continue
                aggregates.append(
                    DailyAggregate(
                        provider=self.provider,
                        day=day,
                        native_model=str(model),
                        machine=self._machine,
                        total_tokens=total,
                        provenance=Provenance.STATS_CACHE,
                    )
                )
        return aggregates

    def _event_from_record(self, record: dict[str, Any]) -> UsageEvent | None:
        """Normalize one JSONL record, or return None if not a usage record.

        Returns None for non-assistant lines, synthetic models, and
        records without usage data; raises for structurally broken records
        (caller counts them as malformed).
        """
        if record.get("type") != "assistant":
            return None
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return None
        model = message.get("model")
        if not isinstance(model, str) or not model or model in _SYNTHETIC_MODELS:
            return None
        event_id = record.get("requestId") or message.get("id")
        if not isinstance(event_id, str) or not event_id:
            return None

        timestamp = datetime.fromisoformat(str(record["timestamp"]))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)

        cache_creation = usage.get("cache_creation")
        if isinstance(cache_creation, dict):
            short_write = int(cache_creation.get("ephemeral_5m_input_tokens", 0) or 0)
            long_write = int(cache_creation.get("ephemeral_1h_input_tokens", 0) or 0)
        else:
            # Older records lack the TTL breakdown; the combined count is
            # 5-minute cache by default in Claude Code.
            short_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
            long_write = 0

        extra: dict[str, Any] = {}
        server_tools = usage.get("server_tool_use")
        if isinstance(server_tools, dict):
            for counter in ("web_search_requests", "web_fetch_requests"):
                value = server_tools.get(counter)
                if isinstance(value, int) and value > 0:
                    extra[counter] = value

        return UsageEvent(
            event_id=event_id,
            provider=self.provider,
            native_model=model,
            ts=timestamp,
            machine=self._machine,
            session_id=_optional_str(record.get("sessionId")),
            project=_optional_str(record.get("cwd")),
            git_branch=_optional_str(record.get("gitBranch")),
            client_version=_optional_str(record.get("version")),
            entrypoint=_optional_str(record.get("entrypoint")),
            is_sidechain=bool(record.get("isSidechain", False)),
            session_kind=_optional_str(record.get("sessionKind")),
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            cache_write_short_tokens=short_write,
            cache_write_long_tokens=long_write,
            service_tier=_optional_str(usage.get("service_tier")),
            speed=_optional_str(usage.get("speed")),
            provenance=Provenance.LOCAL_ESTIMATE,
            extra=extra,
        )


def _optional_str(value: Any) -> str | None:
    """Return ``value`` as a non-empty string, else None."""
    if isinstance(value, str) and value:
        return value
    return None
