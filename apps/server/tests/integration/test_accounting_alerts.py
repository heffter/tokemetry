"""Accounting-gap alert tests (Task 68.3).

Two evaluators over v2 accounting data:

- ``unpriced_events`` -- active ``computed_costs`` still ``unpriced``/``partial``
  in the window, with count thresholds, dimension filters, top-offender
  ordering, and the open ``unpriced_usage`` data-quality link.
- ``unknown_model`` -- reworked off the old NULL-cost heuristic onto the model
  registry lifecycle: an event fires only when its model is unregistered or
  ``lifecycle='unknown'``, not merely unpriced. Includes a migration-behavior
  check that a pre-existing rule still fires on the reworked signal.

Context assertions confirm the alerts stay content-free (catalog identifiers
only, no event content).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import make_v1_event
from sqlalchemy.ext.asyncio import AsyncSession
from tokemetry_server.db import models
from tokemetry_server.services.alerting.rules import evaluate_rule
from tokemetry_server.services.computed_costs import record_cost

_NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
_RECENT = _NOW - timedelta(hours=2)


def _rule(kind: str, **kwargs: object) -> models.AlertRule:
    defaults: dict[str, object] = {"name": kind, "kind": kind, "cooldown_seconds": 0, "config": {}}
    defaults.update(kwargs)
    return models.AlertRule(**defaults)


async def _event(
    session: AsyncSession,
    event_id: str,
    *,
    provider: str = "anthropic",
    model: str = "m",
    ts: datetime = _RECENT,
    **fields: object,
) -> None:
    """Add a recent final-attempt ledger row."""
    session.add(
        make_v1_event(provider=provider, event_id=event_id, model=model, ts=ts, **fields)
    )


async def _priced(
    session: AsyncSession,
    event_id: str,
    status: str,
    *,
    provider: str = "anthropic",
    model: str = "m",
) -> None:
    """Add a ledger event plus a computed-cost row with the given status."""
    await _event(session, event_id, provider=provider, model=model)
    await session.flush()
    await record_cost(
        session, provider, event_id, amount=None, cost_status=status, pricing_version="1"
    )


def _register_model(
    session: AsyncSession, provider: str, native_model: str, lifecycle: str
) -> None:
    """Register a model row with an explicit lifecycle."""
    session.add(
        models.Model(
            provider=provider,
            native_model_id=native_model,
            lifecycle=lifecycle,
            first_seen=_RECENT,
            last_seen=_RECENT,
        )
    )


# --------------------------------------------------------------------------- #
# unpriced_events
# --------------------------------------------------------------------------- #

async def test_unpriced_events_fires_on_unpriced_and_partial(
    async_session: AsyncSession,
) -> None:
    await _priced(async_session, "u1", "unpriced")
    await _priced(async_session, "p1", "partial")
    await _priced(async_session, "ok", "priced")  # priced -> excluded
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unpriced_events"), _NOW)

    assert finding is not None
    assert finding.context["unpriced_events"] == 2


async def test_unpriced_events_silent_when_all_priced(
    async_session: AsyncSession,
) -> None:
    await _priced(async_session, "ok1", "priced")
    await _priced(async_session, "ok2", "estimated")
    await async_session.commit()

    assert await evaluate_rule(async_session, _rule("unpriced_events"), _NOW) is None


async def test_unpriced_events_count_thresholds(async_session: AsyncSession) -> None:
    for i in range(3):
        await _priced(async_session, f"u{i}", "unpriced")
    await async_session.commit()

    # warn 2 / crit 5: 3 unpriced -> warning, not yet critical.
    warn = await evaluate_rule(
        async_session,
        _rule("unpriced_events", warn_threshold=Decimal("2"), crit_threshold=Decimal("5")),
        _NOW,
    )
    assert warn is not None
    assert warn.severity == "warning"

    # warn 2 / crit 3: 3 unpriced -> critical.
    crit = await evaluate_rule(
        async_session,
        _rule("unpriced_events", warn_threshold=Decimal("2"), crit_threshold=Decimal("3")),
        _NOW,
    )
    assert crit is not None
    assert crit.severity == "critical"


async def test_unpriced_events_respects_provider_filter(
    async_session: AsyncSession,
) -> None:
    await _priced(async_session, "a1", "unpriced", provider="anthropic")
    await _priced(async_session, "o1", "unpriced", provider="openai")
    await async_session.commit()

    scoped = await evaluate_rule(
        async_session,
        _rule("unpriced_events", config={"filters": {"provider": ["anthropic"]}}),
        _NOW,
    )

    assert scoped is not None
    assert scoped.context["unpriced_events"] == 1
    assert scoped.context["scoped_dimensions"] == ["provider"]


async def test_unpriced_events_top_offenders_ordered(
    async_session: AsyncSession,
) -> None:
    for i in range(3):
        await _priced(async_session, f"big{i}", "unpriced", model="mystery-9")
    await _priced(async_session, "small", "partial", model="mystery-1")
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unpriced_events"), _NOW)

    assert finding is not None
    offenders = finding.context["top_offenders"]
    assert offenders[0] == {"provider": "anthropic", "model": "mystery-9", "count": 3}
    assert offenders[1] == {"provider": "anthropic", "model": "mystery-1", "count": 1}


async def test_unpriced_events_links_open_data_quality(
    async_session: AsyncSession,
) -> None:
    await _priced(async_session, "u1", "unpriced")
    async_session.add(
        models.DataQualityEvent(
            kind="unpriced_usage", subject="anthropic/m", detail={}, ts=_RECENT, resolved=False
        )
    )
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unpriced_events"), _NOW)

    assert finding is not None
    assert finding.context["open_data_quality_events"] == 1


# --------------------------------------------------------------------------- #
# unknown_model (reworked onto the registry lifecycle signal)
# --------------------------------------------------------------------------- #

async def test_unknown_model_fires_for_unregistered_model(
    async_session: AsyncSession,
) -> None:
    # No registry row for the model -> treated as unknown.
    await _event(async_session, "e1", model="brand-new-9")
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unknown_model"), _NOW)

    assert finding is not None
    assert finding.context["unknown_model_events"] == 1


async def test_unknown_model_fires_for_lifecycle_unknown(
    async_session: AsyncSession,
) -> None:
    _register_model(async_session, "anthropic", "seen-but-unknown", "unknown")
    await _event(async_session, "e1", model="seen-but-unknown")
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unknown_model"), _NOW)

    assert finding is not None
    assert finding.context["unknown_model_events"] == 1


async def test_unknown_model_silent_for_known_model(
    async_session: AsyncSession,
) -> None:
    # A known (active) model that merely lacks a price is NOT an unknown model.
    _register_model(async_session, "anthropic", "claude-known", "active")
    await _event(async_session, "e1", model="claude-known")
    await async_session.flush()
    await record_cost(
        async_session, "anthropic", "e1", amount=None, cost_status="unpriced", pricing_version="1"
    )
    await async_session.commit()

    assert await evaluate_rule(async_session, _rule("unknown_model"), _NOW) is None


async def test_unknown_model_deprecated_and_retired_are_known(
    async_session: AsyncSession,
) -> None:
    _register_model(async_session, "anthropic", "old-model", "deprecated")
    _register_model(async_session, "anthropic", "gone-model", "retired")
    await _event(async_session, "d1", model="old-model")
    await _event(async_session, "r1", model="gone-model")
    await async_session.commit()

    assert await evaluate_rule(async_session, _rule("unknown_model"), _NOW) is None


async def test_unknown_model_respects_provider_filter(
    async_session: AsyncSession,
) -> None:
    await _event(async_session, "a1", provider="anthropic", model="x")
    await _event(async_session, "o1", provider="openai", model="y")
    await async_session.commit()

    scoped = await evaluate_rule(
        async_session,
        _rule("unknown_model", config={"filters": {"provider": ["openai"]}}),
        _NOW,
    )

    assert scoped is not None
    assert scoped.context["unknown_model_events"] == 1
    assert scoped.context["scoped_dimensions"] == ["provider"]


async def test_unknown_model_top_offenders_and_dq_link(
    async_session: AsyncSession,
) -> None:
    for i in range(2):
        await _event(async_session, f"m{i}", model="mystery-9")
    await _event(async_session, "s1", model="mystery-1")
    async_session.add(
        models.DataQualityEvent(
            kind="unknown_model",
            subject="anthropic/mystery-9",
            detail={"provider": "anthropic", "native_model": "mystery-9"},
            ts=_RECENT,
            resolved=False,
        )
    )
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unknown_model"), _NOW)

    assert finding is not None
    assert finding.context["top_offenders"][0] == {
        "provider": "anthropic",
        "model": "mystery-9",
        "count": 2,
    }
    assert finding.context["open_data_quality_events"] == 1


async def test_pre_existing_unknown_model_rule_fires_on_reworked_signal(
    async_session: AsyncSession,
) -> None:
    """Migration behavior: a rule stored before the rework still fires.

    A rule row created against the old NULL-cost evaluator carries only a name
    and kind (no config, no thresholds). It must still fire when a genuinely
    unknown model appears under the reworked registry-lifecycle signal.
    """
    legacy_rule = models.AlertRule(name="unknown_model", kind="unknown_model")
    await _event(async_session, "e1", model="mystery-legacy")
    await async_session.commit()

    finding = await evaluate_rule(async_session, legacy_rule, _NOW)

    assert finding is not None
    assert finding.severity == "warning"
    assert finding.context["unknown_model_events"] == 1


# --------------------------------------------------------------------------- #
# Content-free context
# --------------------------------------------------------------------------- #

async def test_accounting_context_is_content_free(async_session: AsyncSession) -> None:
    await _priced(async_session, "u1", "unpriced", model="mystery-9")
    await async_session.commit()

    finding = await evaluate_rule(async_session, _rule("unpriced_events"), _NOW)

    assert finding is not None
    # Only catalog identifiers and counts -- never event content.
    allowed = {
        "unpriced_events",
        "top_offenders",
        "open_data_quality_events",
        "scoped_dimensions",
    }
    assert set(finding.context) == allowed
    for offender in finding.context["top_offenders"]:
        assert set(offender) == {"provider", "model", "count"}
