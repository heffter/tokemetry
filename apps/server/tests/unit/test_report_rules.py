"""Unit tests for the optimization report rule engine."""

from typing import Any

from tokemetry_server.services.report import (
    DimensionRow,
    Scorecard,
    evaluate_rules,
)


def _scorecard(**overrides: Any) -> Scorecard:
    base: dict[str, Any] = {
        "total_tokens": 10_000_000,
        "input_tokens": 1_000_000,
        "output_tokens": 200_000,
        "cache_read_tokens": 9_000_000,
        "cache_write_tokens": 500_000,
        "cache_hit_rate": 0.9,
        "verbosity_ratio": 0.2,
        "median_tokens_per_turn": 1000.0,
        "sidechain_share": 0.1,
        "unattributed_share": 0.05,
        "session_count": 50,
        "machine_count": 2,
        "top_models": [("claude-opus-4-8", 0.4)],
    }
    base.update(overrides)
    return Scorecard(**base)


def _dim(name: str, **overrides: Any) -> DimensionRow:
    base: dict[str, Any] = {
        "name": name,
        "total_tokens": 5_000_000,
        "cache_hit_rate": 0.9,
        "median_tokens_per_turn": 1000.0,
        "verbosity_ratio": 0.2,
        "sidechain_share": 0.1,
        "session_count": 20,
    }
    base.update(overrides)
    return DimensionRow(**base)


def _ids(recs: list[Any]) -> set[str]:
    return {r.id for r in recs}


def test_healthy_scorecard_has_no_recommendations() -> None:
    assert evaluate_rules(_scorecard(), [], []) == []


def test_low_cache_hit_rate_flagged() -> None:
    recs = evaluate_rules(_scorecard(cache_hit_rate=0.5), [], [])
    assert "cache_hit_rate" in _ids(recs)


def test_verbosity_flagged_with_impact() -> None:
    recs = evaluate_rules(
        _scorecard(verbosity_ratio=0.8, input_tokens=1_000_000, output_tokens=800_000),
        [],
        [],
    )
    verbosity = next(r for r in recs if r.id == "verbosity")
    # output(800k) - 0.3 * input(1M) = 500k reclaimable
    assert verbosity.impact_tokens == 500_000


def test_opus_concentration_flags_routing() -> None:
    recs = evaluate_rules(
        _scorecard(top_models=[("claude-opus-4-8", 0.72)]), [], []
    )
    assert "model_routing" in _ids(recs)


def test_machine_config_drift_flagged() -> None:
    machines = [
        _dim("box-a", cache_hit_rate=0.92),
        _dim("box-b", cache_hit_rate=0.90),
        _dim("box-c", cache_hit_rate=0.55),
    ]
    recs = evaluate_rules(_scorecard(), [], machines)
    drift = next(r for r in recs if r.id == "config_drift")
    assert "box-c" in drift.affected


def test_recommendations_sorted_by_severity() -> None:
    recs = evaluate_rules(
        _scorecard(
            cache_hit_rate=0.4,
            verbosity_ratio=0.9,
            unattributed_share=0.3,
        ),
        [],
        [],
    )
    ranks = {"critical": 0, "warning": 1, "info": 2}
    severities = [ranks[r.severity] for r in recs]
    assert severities == sorted(severities)
