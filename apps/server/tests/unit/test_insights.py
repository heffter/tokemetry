"""Unit tests for statistical session-anomaly detection."""

from tokemetry_server.services.insights import SessionAgg, classify_anomalies


def _agg(
    i: int, total: int, cost: float | None, read: int, inp: int
) -> SessionAgg:
    return SessionAgg(
        session_id=f"s{i}",
        project="proj",
        total_tokens=total,
        cost_usd=cost,
        cache_read_tokens=read,
        input_tokens=inp,
    )


def test_not_enough_data_below_min_sessions() -> None:
    aggs = [_agg(i, 100_000, 1.0, 95_000, 5_000) for i in range(5)]
    report = classify_anomalies(aggs)
    assert report.enough_data is False
    assert report.anomalies == []


def _baseline() -> list[SessionAgg]:
    # 20 well-cached ~100k-token sessions form the normal population.
    return [_agg(i, 100_000 + i * 500, 1.0, 95_000, 5_000) for i in range(20)]


def test_high_token_low_cache_outlier_is_flagged_first() -> None:
    aggs = _baseline()
    # A huge, expensive, poorly-cached session.
    aggs.append(_agg(99, 6_000_000, 50.0, 100_000, 5_900_000))
    report = classify_anomalies(aggs)

    assert report.enough_data is True
    assert report.session_count == 21
    assert report.anomalies, "expected at least one anomaly"
    top = report.anomalies[0]
    assert top.session_id == "s99"
    assert "high tokens" in top.reasons
    assert "low cache reuse" in top.reasons
    # severity = cost * (1 - cache_hit_rate) is the largest here.
    assert top.severity_score > 0


def test_normal_population_has_no_anomalies() -> None:
    report = classify_anomalies(_baseline())
    assert report.enough_data is True
    assert report.anomalies == []
