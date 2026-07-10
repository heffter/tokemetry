"""Unit tests for the LLM-ready Markdown report export."""

from dataclasses import dataclass
from datetime import date

from tokemetry_server.services.report import (
    DimensionRow,
    Recommendation,
    Report,
    Scorecard,
)
from tokemetry_server.services.report_export import (
    FULL_SESSION_LIMIT,
    render_report_markdown,
)


def _report() -> Report:
    scorecard = Scorecard(
        total_tokens=10_000_000,
        input_tokens=1_000_000,
        output_tokens=200_000,
        cache_read_tokens=9_000_000,
        cache_write_tokens=500_000,
        cache_hit_rate=0.55,
        verbosity_ratio=0.2,
        median_tokens_per_turn=1000.0,
        sidechain_share=0.1,
        unattributed_share=0.05,
        session_count=50,
        machine_count=2,
        top_models=[("claude-opus-4-8", 0.6)],
    )
    project = DimensionRow(
        name="tokemetry",
        total_tokens=6_000_000,
        cache_hit_rate=0.5,
        median_tokens_per_turn=1200.0,
        verbosity_ratio=0.25,
        sidechain_share=0.08,
        session_count=30,
    )
    machine = DimensionRow(
        name="box-a",
        total_tokens=10_000_000,
        cache_hit_rate=0.55,
        median_tokens_per_turn=1000.0,
        verbosity_ratio=0.2,
        sidechain_share=0.1,
        session_count=50,
    )
    rec = Recommendation(
        id="cache_hit_rate",
        title="Improve prompt-cache hit rate",
        severity="warning",
        evidence="Cache-hit-rate is 55% (target >=85%).",
        affected=["tokemetry"],
        impact_tokens=250_000,
        effort="M",
    )
    return Report(
        start=date(2026, 6, 1),
        end=date(2026, 6, 30),
        scorecard=scorecard,
        projects=[project],
        machines=[machine],
        trend=[("2026-06-01", 1_000_000), ("2026-06-02", 2_000_000)],
        recommendations=[rec],
    )


@dataclass
class _FakeSession:
    session_id: str
    machine: str | None
    project: str | None
    message_count: int
    total_tokens: int


@dataclass
class _FakeAnomaly:
    session_id: str
    project: str | None
    reasons: list[str]
    severity_score: float
    total_tokens: int
    cache_hit_rate: float


def test_compact_export_embeds_prompt_and_tables() -> None:
    md = render_report_markdown(_report(), size="compact")
    # The embedded analysis prompt and its Max-subscription framing.
    assert "## Your task" in md
    assert "Max subscription" in md
    assert "Cache-hit-rate >= 85%" in md
    # The data dictionary and all core tables.
    assert "## Data dictionary" in md
    assert "## Scorecard" in md
    assert "## Recommendations (rule engine)" in md
    assert "## By project" in md
    assert "## By machine" in md
    assert "## Daily trend" in md
    # A recommendation renders with its evidence.
    assert "Improve prompt-cache hit rate" in md
    # Compact omits the full-only sections.
    assert "## Top sessions" not in md
    assert "## Anomalies" not in md


def test_compact_export_stays_within_line_budget() -> None:
    md = render_report_markdown(_report(), size="compact")
    # Compact must stay small enough to paste anywhere.
    assert len(md.splitlines()) < 200


def test_full_export_embeds_sessions_and_anomalies() -> None:
    sessions = [
        _FakeSession(
            session_id=f"sess-{i:04d}",
            machine="box-a",
            project="tokemetry",
            message_count=10,
            total_tokens=(i + 1) * 1000,
        )
        for i in range(FULL_SESSION_LIMIT + 20)
    ]
    anomalies = [
        _FakeAnomaly(
            session_id="sess-9999",
            project="tokemetry",
            reasons=["10x tokens vs baseline"],
            severity_score=3.0,
            total_tokens=5_000_000,
            cache_hit_rate=0.2,
        )
    ]
    md = render_report_markdown(
        _report(), size="full", sessions=sessions, anomalies=anomalies
    )
    assert "## Top sessions" in md
    assert "## Anomalies" in md
    assert "10x tokens vs baseline" in md
    # The session table is capped at FULL_SESSION_LIMIT rows. Scope the count to
    # the "Top sessions" section so the anomaly table's session id is excluded.
    sessions_block = md.split("## Top sessions", 1)[1].split("## Anomalies", 1)[0]
    session_rows = [
        ln for ln in sessions_block.splitlines() if ln.startswith("| sess-")
    ]
    assert len(session_rows) == FULL_SESSION_LIMIT
    # The heaviest session (last index) is included; the lightest is dropped.
    assert "sess-0119" in md
    assert "sess-0000" not in md
