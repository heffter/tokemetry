"""Unit tests for per-session efficiency statistics."""

from tokemetry_server.services.session_stats import compute_session_stats


def test_empty_session() -> None:
    stats = compute_session_stats([], [], [])
    assert stats.tokens_per_turn == 0.0
    assert stats.cache_hit_rate == 0.0
    assert stats.context_growth == 1.0
    assert stats.inflection_index is None


def test_tokens_per_turn_and_cache_hit_rate() -> None:
    stats = compute_session_stats([100, 200], [50, 150], [50, 50])
    assert stats.tokens_per_turn == 150.0
    # read=200, prompt=read+input=200+100=300 -> 2/3
    assert abs(stats.cache_hit_rate - 2 / 3) < 1e-9


def test_context_growth_ratio() -> None:
    stats = compute_session_stats([100, 100, 300, 300], [0, 0, 0, 0], [0, 0, 0, 0])
    assert stats.context_growth == 3.0


def test_inflection_detected_at_spike() -> None:
    # median 100, 2x threshold 200; first turn above it is index 3.
    stats = compute_session_stats([100, 100, 100, 500], [0] * 4, [0] * 4)
    assert stats.inflection_index == 3


def test_no_inflection_for_short_session() -> None:
    stats = compute_session_stats([100, 500], [0, 0], [0, 0])
    assert stats.inflection_index is None


def test_no_inflection_when_flat() -> None:
    stats = compute_session_stats([100, 100, 100, 100], [0] * 4, [0] * 4)
    assert stats.inflection_index is None
