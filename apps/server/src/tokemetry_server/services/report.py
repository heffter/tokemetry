"""Token-optimization report: metrics + a rule engine of recommendations.

Aggregates usage over a date range into a global scorecard and per-project /
per-machine breakdowns, then runs a pure rule engine (:func:`evaluate_rules`)
that maps metric thresholds to actionable recommendations with an estimated
number of reclaimable tokens. Recommendation thresholds come from community
best practice (prompt-caching hygiene, verbosity, subagent isolation, model
routing) and are tunable module constants.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_core.projects import DEFAULT_ROOTS, project_group

from tokemetry_server.db import models

# --- Rule thresholds (tunable) ---
#: Cache-hit-rate below this is flagged (healthy Claude Code sits ~0.85-0.95).
CACHE_HIT_WARN = 0.7
#: Per-machine cache-hit-rate this far below the fleet median flags config drift.
DRIFT_MARGIN = 0.15
#: Mean output tokens per turn above this suggests over-verbose responses.
#: Derived from the fleet distribution (~p90 of substantial sessions, ~2x the
#: typical ~1k/turn). Cache-invariant: unlike an output/input ratio it does not
#: distort when prompt-cache reads dominate the input (which inflates the ratio
#: toward infinity as fresh input shrinks).
OUTPUT_PER_TURN_WARN = 2000.0
#: A healthy output-per-turn baseline (~the fleet mean) used to estimate the
#: reclaimable output tokens above target.
OUTPUT_PER_TURN_TARGET = 1000.0
#: Sidechain (subagent) token share below this with heavy usage suggests no
#: exploration isolation.
SIDECHAIN_MIN = 0.02
#: Unattributed token share above this is worth surfacing.
UNATTRIBUTED_WARN = 0.15
#: A single model above this token share suggests over-reliance / mis-routing.
MODEL_CONCENTRATION_WARN = 0.6
#: Ignore tiny dimensions when flagging (too little data to act on).
MIN_DIMENSION_TOKENS = 1_000_000


@dataclass(frozen=True)
class Scorecard:
    """Global usage metrics over the reporting range."""

    total_tokens: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_turns: int
    cache_hit_rate: float
    #: Mean output tokens per assistant turn. The cache-invariant verbosity
    #: signal (does not divide by the cache-distorted input).
    output_per_turn: float
    #: output / (input + cache_read): output's bounded share of everything the
    #: model read. For display; never explodes under prompt caching.
    generation_share: float
    median_tokens_per_turn: float
    sidechain_share: float
    unattributed_share: float
    session_count: int
    machine_count: int
    top_models: list[tuple[str, float]]


@dataclass(frozen=True)
class DimensionRow:
    """Per-project or per-machine rollup."""

    name: str
    total_tokens: int
    cache_hit_rate: float
    median_tokens_per_turn: float
    output_per_turn: float
    generation_share: float
    sidechain_share: float
    session_count: int


@dataclass(frozen=True)
class Recommendation:
    """One ranked optimization recommendation."""

    id: str
    title: str
    severity: str
    evidence: str
    affected: list[str]
    impact_tokens: int | None
    effort: str


@dataclass(frozen=True)
class Report:
    """The full optimization report over a range."""

    start: date
    end: date
    scorecard: Scorecard
    projects: list[DimensionRow]
    machines: list[DimensionRow]
    trend: list[tuple[str, int]]
    recommendations: list[Recommendation]


_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def evaluate_rules(
    scorecard: Scorecard,
    projects: Sequence[DimensionRow],
    machines: Sequence[DimensionRow],
) -> list[Recommendation]:
    """Map metric thresholds to ranked recommendations (pure)."""
    recs: list[Recommendation] = []

    if scorecard.cache_hit_rate < CACHE_HIT_WARN and scorecard.total_tokens:
        # Cache reads cost ~10% of a fresh input token; the low hit rate means
        # context was re-written rather than read. Rough reclaim: the cache
        # writes that a stable prefix would have turned into reads.
        recs.append(
            Recommendation(
                id="cache_hit_rate",
                title="Improve prompt-cache hit rate",
                severity="warning",
                evidence=(
                    f"Cache-hit-rate is {scorecard.cache_hit_rate:.0%} "
                    f"(target >=85%). Keep the stable prefix (system prompt, "
                    f"tools, CLAUDE.md) byte-identical across turns and avoid "
                    f"mid-session model/tool changes that bust the cache."
                ),
                affected=[
                    p.name
                    for p in projects
                    if p.cache_hit_rate < CACHE_HIT_WARN
                    and p.total_tokens >= MIN_DIMENSION_TOKENS
                ],
                impact_tokens=int(scorecard.cache_write_tokens * 0.5),
                effort="M",
            )
        )

    if scorecard.output_per_turn > OUTPUT_PER_TURN_WARN and scorecard.total_turns:
        reclaim = max(
            0,
            int(
                scorecard.output_tokens
                - OUTPUT_PER_TURN_TARGET * scorecard.total_turns
            ),
        )
        recs.append(
            Recommendation(
                id="verbosity",
                title="Reduce output verbosity",
                severity="warning",
                evidence=(
                    f"Output averages {scorecard.output_per_turn:,.0f} tokens per "
                    f"turn (healthy is ~{OUTPUT_PER_TURN_TARGET:,.0f}). Ask for "
                    f"terser answers and route bulky build/test logs to a file, "
                    f"showing only the failing lines."
                ),
                affected=[
                    p.name
                    for p in projects
                    if p.output_per_turn > OUTPUT_PER_TURN_WARN
                    and p.total_tokens >= MIN_DIMENSION_TOKENS
                ],
                impact_tokens=reclaim,
                effort="S",
            )
        )

    if (
        scorecard.sidechain_share < SIDECHAIN_MIN
        and scorecard.total_tokens >= MIN_DIMENSION_TOKENS
    ):
        recs.append(
            Recommendation(
                id="subagents",
                title="Delegate exploration to subagents",
                severity="info",
                evidence=(
                    f"Only {scorecard.sidechain_share:.0%} of tokens ran in "
                    f"subagents. Heavy file-read/search phases should run in a "
                    f"subagent so the results summarize back instead of filling "
                    f"the main context."
                ),
                affected=[],
                impact_tokens=None,
                effort="M",
            )
        )

    if scorecard.unattributed_share > UNATTRIBUTED_WARN:
        recs.append(
            Recommendation(
                id="unattributed",
                title="Attribute unclassified usage",
                severity="info",
                evidence=(
                    f"{scorecard.unattributed_share:.0%} of tokens have no "
                    f"project. This is historical bootstrap data or runs outside "
                    f"a project root; it cannot be optimized per-project until "
                    f"attributed."
                ),
                affected=[],
                impact_tokens=None,
                effort="S",
            )
        )

    for model, share in scorecard.top_models:
        if share > MODEL_CONCENTRATION_WARN and "opus" in model.lower():
            recs.append(
                Recommendation(
                    id="model_routing",
                    title="Route mechanical work to a cheaper model",
                    severity="info",
                    evidence=(
                        f"{model} is {share:.0%} of tokens. Route search, "
                        f"format, and lookup work to Haiku and reserve the "
                        f"largest model for architecture and hard debugging."
                    ),
                    affected=[],
                    impact_tokens=None,
                    effort="S",
                )
            )
            break

    # Per-machine config drift: a machine well below the fleet's cache-hit-rate
    # usually has a different CLAUDE.md / MCP set / model default.
    rates = [m.cache_hit_rate for m in machines if m.total_tokens >= MIN_DIMENSION_TOKENS]
    if len(rates) >= 2:
        fleet = statistics.median(rates)
        drifters = [
            m.name
            for m in machines
            if m.total_tokens >= MIN_DIMENSION_TOKENS
            and m.cache_hit_rate < fleet - DRIFT_MARGIN
        ]
        if drifters:
            recs.append(
                Recommendation(
                    id="config_drift",
                    title="Fix machine configuration drift",
                    severity="warning",
                    evidence=(
                        f"{', '.join(drifters)} have a cache-hit-rate well below "
                        f"the fleet median ({fleet:.0%}) -- likely a different "
                        f"CLAUDE.md, MCP set, or default model on those machines."
                    ),
                    affected=drifters,
                    impact_tokens=None,
                    effort="M",
                )
            )

    recs.sort(
        key=lambda r: (_SEVERITY_RANK.get(r.severity, 9), -(r.impact_tokens or 0))
    )
    return recs


@dataclass
class _SessionAgg:
    """Mutable per-session accumulator during aggregation."""

    machine: str | None
    project: str | None
    input: int
    output: int
    cache_read: int
    cache_write: int
    total: int
    turns: int
    sidechain: int


def _cache_hit_rate(cache_read: int, prompt_input: int) -> float:
    denom = cache_read + prompt_input
    return cache_read / denom if denom else 0.0


def _dimension_rows(
    groups: dict[str, list[_SessionAgg]],
) -> list[DimensionRow]:
    """Build sorted DimensionRows from grouped session aggregates."""
    rows: list[DimensionRow] = []
    for name, sessions in groups.items():
        total = sum(s.total for s in sessions)
        cache_read = sum(s.cache_read for s in sessions)
        inp = sum(s.input for s in sessions)
        out = sum(s.output for s in sessions)
        side = sum(s.sidechain for s in sessions)
        turns = sum(s.turns for s in sessions)
        per_turn = [s.total / s.turns for s in sessions if s.turns]
        rows.append(
            DimensionRow(
                name=name,
                total_tokens=total,
                cache_hit_rate=_cache_hit_rate(cache_read, inp),
                median_tokens_per_turn=statistics.median(per_turn) if per_turn else 0.0,
                output_per_turn=out / turns if turns else 0.0,
                generation_share=out / (inp + cache_read) if (inp + cache_read) else 0.0,
                sidechain_share=side / total if total else 0.0,
                session_count=len(sessions),
            )
        )
    rows.sort(key=lambda r: r.total_tokens, reverse=True)
    return rows


async def build_report(
    session: AsyncSession,
    start: date,
    end: date,
    roots: Sequence[str] = DEFAULT_ROOTS,
) -> Report:
    """Aggregate usage over a range and evaluate the recommendation rules."""
    event = models.UsageEvent
    start_ts = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_ts = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)
    total_expr = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    in_range = (event.ts >= start_ts, event.ts <= end_ts)

    rows = (
        await session.execute(
            select(
                event.session_id,
                func.min(event.machine),
                func.min(event.project),
                func.coalesce(func.sum(event.input_tokens), 0),
                func.coalesce(func.sum(event.output_tokens), 0),
                func.coalesce(func.sum(event.cache_read_tokens), 0),
                func.coalesce(
                    func.sum(event.cache_write_short_tokens + event.cache_write_long_tokens),
                    0,
                ),
                func.coalesce(func.sum(total_expr), 0),
                func.count(),
                func.coalesce(
                    func.sum(case((event.is_sidechain, total_expr), else_=0)), 0
                ),
            )
            .where(*in_range, event.session_id.is_not(None))
            .group_by(event.session_id)
        )
    ).all()

    sessions = [
        _SessionAgg(
            machine=r[1],
            project=r[2],
            input=int(r[3] or 0),
            output=int(r[4] or 0),
            cache_read=int(r[5] or 0),
            cache_write=int(r[6] or 0),
            total=int(r[7] or 0),
            turns=int(r[8] or 0),
            sidechain=int(r[9] or 0),
        )
        for r in rows
    ]

    model_rows = (
        await session.execute(
            select(event.model, func.coalesce(func.sum(total_expr), 0))
            .where(*in_range)
            .group_by(event.model)
        )
    ).all()
    model_tokens = {str(m): int(t or 0) for m, t in model_rows}
    model_total = sum(model_tokens.values()) or 1
    top_models = sorted(
        ((m, t / model_total) for m, t in model_tokens.items()),
        key=lambda mt: mt[1],
        reverse=True,
    )[:5]

    trend = await _daily_trend(session, start_ts, end_ts)

    total = sum(s.total for s in sessions)
    inp = sum(s.input for s in sessions)
    out = sum(s.output for s in sessions)
    cache_read = sum(s.cache_read for s in sessions)
    cache_write = sum(s.cache_write for s in sessions)
    side = sum(s.sidechain for s in sessions)
    unattributed = sum(s.total for s in sessions if not s.project)
    total_turns = sum(s.turns for s in sessions)
    per_turn = [s.total / s.turns for s in sessions if s.turns]

    scorecard = Scorecard(
        total_tokens=total,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        total_turns=total_turns,
        cache_hit_rate=_cache_hit_rate(cache_read, inp),
        output_per_turn=out / total_turns if total_turns else 0.0,
        generation_share=out / (inp + cache_read) if (inp + cache_read) else 0.0,
        median_tokens_per_turn=statistics.median(per_turn) if per_turn else 0.0,
        sidechain_share=side / total if total else 0.0,
        unattributed_share=unattributed / total if total else 0.0,
        session_count=len(sessions),
        machine_count=len({s.machine for s in sessions if s.machine}),
        top_models=top_models,
    )

    by_project: dict[str, list[_SessionAgg]] = {}
    by_machine: dict[str, list[_SessionAgg]] = {}
    for s in sessions:
        by_project.setdefault(project_group(s.project, roots), []).append(s)
        by_machine.setdefault(s.machine or "(unknown)", []).append(s)

    projects = _dimension_rows(by_project)
    machines = _dimension_rows(by_machine)

    return Report(
        start=start,
        end=end,
        scorecard=scorecard,
        projects=projects,
        machines=machines,
        trend=trend,
        recommendations=evaluate_rules(scorecard, projects, machines),
    )


async def _daily_trend(
    session: AsyncSession, start_ts: datetime, end_ts: datetime
) -> list[tuple[str, int]]:
    """Per-day token totals from usage_events, bucketed in Python (UTC)."""
    event = models.UsageEvent
    total_expr = (
        event.input_tokens
        + event.output_tokens
        + event.cache_read_tokens
        + event.cache_write_short_tokens
        + event.cache_write_long_tokens
    )
    rows = (
        await session.execute(
            select(event.ts, total_expr).where(
                event.ts >= start_ts, event.ts <= end_ts
            )
        )
    ).all()
    buckets: dict[str, int] = {}
    for ts, tokens in rows:
        aware = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        key = aware.date().isoformat()
        buckets[key] = buckets.get(key, 0) + int(tokens or 0)
    return sorted(buckets.items())


def default_range(days: int = 30) -> tuple[date, date]:
    """A (start, end) range ending today, spanning ``days`` days."""
    end = datetime.now(UTC).date()
    return end - timedelta(days=days), end
