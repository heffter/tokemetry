"""LLM-ready Markdown export of the optimization report.

Renders a self-contained Markdown document a user can paste into any AI agent
to get token-optimization advice. The document has three parts:

1. an embedded analysis prompt framed for a Claude Max subscription -- where
   the weekly / 5-hour usage *caps*, not per-token cost, are the binding
   constraint -- with reference targets and the requested output structure;
2. a data dictionary explaining every metric; and
3. the data itself as Markdown tables.

Two sizes are supported. ``compact`` embeds the scorecard, per-project and
per-machine tables, the daily trend, and the ranked recommendations -- enough
context to reason about, small enough to paste anywhere (~10k tokens). ``full``
additionally embeds the top sessions and the anomaly list for deeper analysis
(bounded so the document stays within a sane paste budget).

The render functions are pure: they take already-aggregated data and return a
string, so they are trivially unit-testable and free of I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from tokemetry_server.services.report import DimensionRow, Recommendation, Report

#: Cap on sessions embedded in a ``full`` export, so the document stays within
#: a reasonable paste budget even on a busy fleet.
FULL_SESSION_LIMIT = 100
#: Cap on anomalies embedded in a ``full`` export.
FULL_ANOMALY_LIMIT = 50


class SessionLike(Protocol):
    """The subset of a session summary the export needs.

    Declared as read-only properties so frozen dataclasses (the actual session
    summaries) structurally satisfy it.
    """

    @property
    def session_id(self) -> str: ...
    @property
    def machine(self) -> str | None: ...
    @property
    def project(self) -> str | None: ...
    @property
    def message_count(self) -> int: ...
    @property
    def total_tokens(self) -> int: ...


class AnomalyLike(Protocol):
    """The subset of an anomaly the export needs."""

    @property
    def session_id(self) -> str: ...
    @property
    def project(self) -> str | None: ...
    @property
    def reasons(self) -> list[str]: ...
    @property
    def severity_score(self) -> float: ...
    @property
    def total_tokens(self) -> int: ...
    @property
    def cache_hit_rate(self) -> float: ...


def _fmt_tokens(value: float) -> str:
    """Format a token count compactly (e.g. 1.2M, 34.5k, 900)."""
    n = float(value)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{round(n)}"


def _fmt_pct(ratio: float) -> str:
    """Format a 0..1 ratio as a whole-number percentage."""
    return f"{ratio * 100:.0f}%"


# The embedded prompt. It frames the task for a Max-subscription user (caps,
# not dollars, are the constraint), states the reference targets the report's
# rules use, and asks for a specific, actionable output structure. Kept in one
# place so the wording is reviewable and consistent between sizes.
_ANALYSIS_PROMPT = """\
## Your task

You are a Claude Code usage-optimization analyst. Below is a token-usage report
for a developer on a **Claude Max subscription**. On Max, the constraint is not
per-token cost -- it is the **rolling 5-hour and weekly usage caps**. Every
token that does not advance the work brings those caps closer and risks a
lock-out mid-session. Optimize for *fewer wasted tokens*, not for a lower bill.

Read the scorecard, the per-project and per-machine tables, the daily trend,
and the rule-engine recommendations, then produce concrete, prioritized advice.

### Reference targets (what "healthy" looks like)

- **Cache-hit-rate >= 85%.** Prompt caching charges a cache *read* at ~10% of a
  fresh input token. A high hit-rate means the stable prefix (system prompt,
  tool definitions, CLAUDE.md) is being reused. A low rate means it is being
  re-sent -- usually from an oversized or churning CLAUDE.md, mid-session tool
  or model switches, or frequent `/clear`.
- **Output ~1,000 tokens/turn.** Verbosity is measured as mean output tokens
  per turn (cache-invariant, unlike an output/input ratio which explodes when
  prompt-cache reads dominate the input). Much above ~2,000/turn means long
  assistant turns: verbose explanations, echoed file contents, unfiltered
  command output.
- **CLAUDE.md and MCP tool definitions are pure overhead** -- they are re-sent
  every turn. Aim for a CLAUDE.md under ~500 tokens; disable MCP servers not in
  active use.
- **Model routing.** Reserve the largest model for architecture and hard
  debugging; route search, formatting, and lookups to a smaller/faster model.
- **Subagents** isolate heavy file-read / search phases so only the summary
  returns to the main context, instead of every read filling it.
- **`/clear` between unrelated tasks.** A long-running context re-sends its full
  history every turn; clearing when switching tasks resets that overhead.

### What to produce

1. **Executive summary** -- 2-3 sentences: overall health and the single biggest
   opportunity.
2. **Top 5 ranked recommendations** -- each with the evidence from the data, the
   concrete change to make, the expected token saving, and effort (S/M/L).
3. **Per-project actions** -- for the heaviest projects, the specific change that
   would help each most.
4. **Per-machine config fixes** -- call out any machine whose cache-hit-rate or
   model mix diverges from the others (a likely CLAUDE.md / MCP / model-default
   drift) and what to align.
5. **Quick wins vs. structural changes** -- separate what takes minutes (trim
   CLAUDE.md, disable an MCP server) from what takes a workflow change (subagent
   adoption, model-routing discipline, `/clear` habits).

Be specific and quantitative. Prefer changes justified by the numbers below.
"""

_DATA_DICTIONARY = """\
## Data dictionary

- **total tokens** -- input + output + cache-read + cache-write over the range.
- **cache-hit-rate** -- cache-read / (cache-read + fresh input). Higher is
  better; target >= 85%.
- **output/turn** -- mean output tokens per assistant turn. The verbosity
  signal; lower is better; healthy is ~1,000, over-verbose is ~2,000+.
  Cache-invariant, so it stays meaningful even at high cache-hit-rates.
- **generation share** -- output / (input + cache-read): output's share of
  everything the model read. A bounded companion to output/turn.
- **tokens/turn** -- median total tokens per assistant turn in a session; a
  proxy for context size and turn heaviness.
- **subagent share** -- fraction of tokens that ran inside a subagent
  (is_sidechain); a proxy for exploration isolation.
- **unattributed share** -- fraction of tokens with no project attribution
  (runs outside a known project root, or historical bootstrap data).
- **severity** -- recommendation priority: critical > warning > info.
- **effort** -- rough implementation cost: S (minutes), M (an afternoon),
  L (a workflow change).
"""


def _scorecard_section(report: Report) -> list[str]:
    """The global scorecard as a two-column Markdown table."""
    s = report.scorecard
    top = ", ".join(f"{m} ({_fmt_pct(share)})" for m, share in s.top_models) or "-"
    lines = [
        "## Scorecard",
        "",
        f"Range: {report.start.isoformat()} to {report.end.isoformat()}",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Total tokens | {_fmt_tokens(s.total_tokens)} |",
        f"| Input tokens | {_fmt_tokens(s.input_tokens)} |",
        f"| Output tokens | {_fmt_tokens(s.output_tokens)} |",
        f"| Cache-read tokens | {_fmt_tokens(s.cache_read_tokens)} |",
        f"| Cache-write tokens | {_fmt_tokens(s.cache_write_tokens)} |",
        f"| Cache-hit-rate | {_fmt_pct(s.cache_hit_rate)} |",
        f"| Output tokens/turn | {_fmt_tokens(s.output_per_turn)} |",
        f"| Generation share | {_fmt_pct(s.generation_share)} |",
        f"| Median tokens/turn | {_fmt_tokens(s.median_tokens_per_turn)} |",
        f"| Subagent share | {_fmt_pct(s.sidechain_share)} |",
        f"| Unattributed share | {_fmt_pct(s.unattributed_share)} |",
        f"| Sessions | {s.session_count} |",
        f"| Machines | {s.machine_count} |",
        f"| Top models | {top} |",
    ]
    return lines


def _dimension_section(title: str, rows: Sequence[DimensionRow]) -> list[str]:
    """A per-project or per-machine breakdown table."""
    lines = [
        f"## {title}",
        "",
        "| Name | Tokens | Cache hit | Out/turn | Gen share | Sessions |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        name = r.name or "(unattributed)"
        lines.append(
            f"| {name} | {_fmt_tokens(r.total_tokens)} "
            f"| {_fmt_pct(r.cache_hit_rate)} "
            f"| {_fmt_tokens(r.output_per_turn)} "
            f"| {_fmt_pct(r.generation_share)} | {r.session_count} |"
        )
    if not rows:
        lines.append("| (no data) | - | - | - | - | - |")
    return lines


def _trend_section(trend: Sequence[tuple[str, int]]) -> list[str]:
    """The daily token trend as a table."""
    lines = ["## Daily trend", "", "| Day | Tokens |", "| --- | ---: |"]
    for day, tokens in trend:
        lines.append(f"| {day} | {_fmt_tokens(tokens)} |")
    if not trend:
        lines.append("| (no data) | - |")
    return lines


def _recommendations_section(recs: Sequence[Recommendation]) -> list[str]:
    """The rule-engine recommendations, ranked, with evidence."""
    lines = ["## Recommendations (rule engine)", ""]
    if not recs:
        lines.append("No optimization issues detected in this range.")
        return lines
    for i, rec in enumerate(recs, start=1):
        impact = (
            f" (~{_fmt_tokens(rec.impact_tokens)} reclaimable/period)"
            if rec.impact_tokens
            else ""
        )
        affected = f" Affects: {', '.join(rec.affected)}." if rec.affected else ""
        lines.append(
            f"{i}. **[{rec.severity}] {rec.title}** (effort {rec.effort}){impact}"
        )
        lines.append(f"   - {rec.evidence}{affected}")
    return lines


def _sessions_section(sessions: Sequence[SessionLike]) -> list[str]:
    """The heaviest sessions (full export only)."""
    top = sorted(sessions, key=lambda s: s.total_tokens, reverse=True)[
        :FULL_SESSION_LIMIT
    ]
    lines = [
        f"## Top sessions (up to {FULL_SESSION_LIMIT})",
        "",
        "| Session | Project | Machine | Turns | Tokens |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for s in top:
        sid = s.session_id[:12]
        lines.append(
            f"| {sid} | {s.project or '-'} | {s.machine or '-'} "
            f"| {s.message_count} | {_fmt_tokens(s.total_tokens)} |"
        )
    if not top:
        lines.append("| (no data) | - | - | - | - |")
    return lines


def _anomalies_section(anomalies: Sequence[AnomalyLike]) -> list[str]:
    """Flagged anomalous sessions (full export only)."""
    top = sorted(anomalies, key=lambda a: a.severity_score, reverse=True)[
        :FULL_ANOMALY_LIMIT
    ]
    lines = [
        f"## Anomalies (up to {FULL_ANOMALY_LIMIT})",
        "",
        "| Session | Project | Tokens | Cache hit | Reasons |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for a in top:
        lines.append(
            f"| {a.session_id[:12]} | {a.project or '-'} "
            f"| {_fmt_tokens(a.total_tokens)} | {_fmt_pct(a.cache_hit_rate)} "
            f"| {'; '.join(a.reasons)} |"
        )
    if not top:
        lines.append("| (none) | - | - | - | - |")
    return lines


def render_report_markdown(
    report: Report,
    *,
    size: str = "compact",
    sessions: Sequence[SessionLike] | None = None,
    anomalies: Sequence[AnomalyLike] | None = None,
) -> str:
    """Render the optimization report as a self-contained Markdown document.

    Args:
        report: The aggregated optimization report.
        size: ``"compact"`` (scorecard, tables, trend, recommendations) or
            ``"full"`` (adds top sessions and anomalies).
        sessions: Session summaries, embedded only when ``size == "full"``.
        anomalies: Detected anomalies, embedded only when ``size == "full"``.

    Returns:
        A Markdown string ready to paste into an AI agent.
    """
    full = size == "full"
    parts: list[str] = [
        "# Claude Code token-usage optimization report",
        "",
        f"Reporting range: **{report.start.isoformat()}** to "
        f"**{report.end.isoformat()}** "
        f"({(report.end - report.start).days + 1} days). "
        f"Export size: **{size}**.",
        "",
        _ANALYSIS_PROMPT,
        "",
        _DATA_DICTIONARY,
        "",
        "\n".join(_scorecard_section(report)),
        "",
        "\n".join(_recommendations_section(report.recommendations)),
        "",
        "\n".join(_dimension_section("By project", report.projects)),
        "",
        "\n".join(_dimension_section("By machine", report.machines)),
        "",
        "\n".join(_trend_section(report.trend)),
    ]
    if full:
        parts += [
            "",
            "\n".join(_sessions_section(sessions or [])),
            "",
            "\n".join(_anomalies_section(anomalies or [])),
        ]
    parts.append("")
    return "\n".join(parts)
