"""Per-session efficiency statistics (metadata only, no message content).

These turn a session's per-turn token series into actionable signals: average
turn size, cache-hit rate, how much context grew over the session, and the
point where a turn ballooned past the session's norm (a natural ``/clear``).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

#: A turn is flagged as an inflection when its total exceeds this multiple of
#: the session's median turn size.
INFLECTION_MULTIPLE = 2.0

#: Sessions shorter than this are too small for a meaningful inflection point.
_MIN_TURNS_FOR_INFLECTION = 4


@dataclass(frozen=True)
class SessionStats:
    """Derived efficiency signals for one session."""

    tokens_per_turn: float
    cache_hit_rate: float
    context_growth: float
    inflection_index: int | None


def compute_session_stats(
    totals: list[int], cache_reads: list[int], inputs: list[int]
) -> SessionStats:
    """Compute efficiency stats from a session's per-turn token series.

    ``cache_hit_rate`` is cache-read over (cache-read + input) prompt tokens.
    ``context_growth`` is the ratio of the late-half mean turn size to the
    early-half mean (>1 means turns grew). ``inflection_index`` is the first
    turn whose total exceeds :data:`INFLECTION_MULTIPLE` times the median.
    """
    turns = len(totals)
    if turns == 0:
        return SessionStats(0.0, 0.0, 1.0, None)
    tokens_per_turn = sum(totals) / turns
    read = sum(cache_reads)
    prompt = read + sum(inputs)
    cache_hit_rate = read / prompt if prompt else 0.0
    return SessionStats(
        tokens_per_turn=tokens_per_turn,
        cache_hit_rate=cache_hit_rate,
        context_growth=_context_growth(totals),
        inflection_index=_inflection_index(totals),
    )


def _context_growth(totals: list[int]) -> float:
    """Ratio of late-half mean turn size to early-half mean (1.0 if too short)."""
    if len(totals) < 2:
        return 1.0
    mid = len(totals) // 2
    early = totals[:mid]
    late = totals[mid:]
    early_mean = sum(early) / len(early)
    late_mean = sum(late) / len(late)
    return late_mean / early_mean if early_mean else 1.0


def _inflection_index(totals: list[int]) -> int | None:
    """First turn exceeding INFLECTION_MULTIPLE x the session median, or None."""
    if len(totals) < _MIN_TURNS_FOR_INFLECTION:
        return None
    median = statistics.median(totals)
    if median <= 0:
        return None
    threshold = median * INFLECTION_MULTIPLE
    for index, value in enumerate(totals):
        if value > threshold:
            return index
    return None
