"""V1 wire-compatibility golden suite (executable form of Epic TOK-1 AC-001).

This suite locks the v1 HTTP contract before any provider-neutral schema work.
It ingests fixed golden fixtures into a fresh database and asserts byte-stable
normalized responses for the data-driven query endpoints, plus structural
invariants for the inherently now-relative endpoints. Any diff is a
compatibility break and must be treated as one (see
``docs/architecture/provider-neutral-baseline.md`` Section 4).

Normalization masks volatile datetime strings (server ``now``, machine
first/last-seen, event/limit timestamps) to ``"<ts>"`` and canonicalizes the
order of object lists, because some query results (for example usage grouped by
session) have no server-defined ordering. Value and membership changes are still
caught; only pure reordering of an unordered collection is tolerated.

Regenerate the golden files after an *intended* contract change with::

    WRITE_GOLDEN=1 uv run pytest apps/server/tests/integration/test_v1_golden.py

Review the resulting diff by hand before committing.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "v1_golden"
_GOLDEN_DIR = _FIXTURES_DIR / "responses"

#: Wide explicit range so history endpoints include the fixed-date fixtures.
_WIDE = "from=2026-01-01&to=2026-12-31"

#: Matches an ISO-8601 datetime (date+time); date-only strings are left intact
#: so day-bucket keys stay meaningful.
_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$")

#: Numeric response fields derived from the server's current clock (e.g. the
#: seconds since a snapshot was taken). Their key is locked but the value is
#: masked so the surrounding contract stays byte-stable.
_VOLATILE_KEYS = frozenset({"age_seconds"})


def _load_payload(name: str) -> dict[str, Any]:
    """Load a golden ingest payload fixture."""
    payload: dict[str, Any] = json.loads(
        (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")
    )
    return payload


def _normalize(obj: Any) -> Any:
    """Mask volatile datetimes/clock values and canonicalize object-list order."""
    if isinstance(obj, dict):
        return {
            key: "<volatile>" if key in _VOLATILE_KEYS else _normalize(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        items = [_normalize(item) for item in obj]
        # Sort lists whose elements are containers: several query results have
        # no server-defined order (e.g. usage-by-session), so a canonical order
        # keeps the snapshot stable without hiding value/membership changes.
        if items and all(isinstance(item, (dict, list)) for item in items):
            items = sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        return items
    if isinstance(obj, str) and _DT_RE.match(obj):
        return "<ts>"
    return obj


def _check_golden(name: str, data: Any) -> None:
    """Compare a normalized response to its committed golden snapshot.

    With ``WRITE_GOLDEN`` set, (re)writes the snapshot instead of asserting.
    """
    normalized = _normalize(data)
    path = _GOLDEN_DIR / f"{name}.json"
    if os.environ.get("WRITE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert normalized == expected, (
        f"v1 golden drift for {name!r}: response no longer matches the locked "
        f"contract. If intended, regenerate with WRITE_GOLDEN=1 and review the diff."
    )


def _get(client: TestClient, auth: dict[str, str], url: str) -> Any:
    """GET a query endpoint, asserting 200."""
    response = client.get(url, headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def seeded_client(client: TestClient, auth: dict[str, str]) -> TestClient:
    """Ingest the golden fixtures and lock the dedupe outcome semantics.

    The events fixture carries a three-line keep-max duplicate group
    (``req_dup``), so the accepted/duplicates_merged counts are part of the v1
    contract asserted here.
    """
    events = client.post(
        "/api/v1/ingest/events", json=_load_payload("ingest_events"), headers=auth
    )
    assert events.status_code == 200, events.text
    assert events.json() == {"accepted": 6, "duplicates_merged": 2}

    limits = client.post(
        "/api/v1/ingest/limits", json=_load_payload("ingest_limits"), headers=auth
    )
    assert limits.status_code == 200, limits.text
    assert limits.json() == {"accepted": 4, "duplicates_merged": 0}

    bootstrap = client.post(
        "/api/v1/ingest/bootstrap", json=_load_payload("ingest_bootstrap"), headers=auth
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert bootstrap.json() == {"accepted": 2, "duplicates_merged": 0}
    return client


# --- Byte-stable golden snapshots (data-driven endpoints) --------------------

@pytest.mark.parametrize("group_by", ["day", "provider", "model", "machine", "project", "session"])
def test_golden_usage_grouped(
    seeded_client: TestClient, auth: dict[str, str], group_by: str
) -> None:
    data = _get(seeded_client, auth, f"/api/v1/usage?group_by={group_by}&{_WIDE}")
    _check_golden(f"usage_{group_by}", data)


def test_golden_sessions_list(seeded_client: TestClient, auth: dict[str, str]) -> None:
    _check_golden("sessions", _get(seeded_client, auth, "/api/v1/sessions"))


def test_golden_session_detail(seeded_client: TestClient, auth: dict[str, str]) -> None:
    _check_golden("session_detail", _get(seeded_client, auth, "/api/v1/sessions/sess-a"))


def test_golden_limits_current(seeded_client: TestClient, auth: dict[str, str]) -> None:
    _check_golden("limits_current", _get(seeded_client, auth, "/api/v1/limits/current"))


def test_golden_summary_overview(seeded_client: TestClient, auth: dict[str, str]) -> None:
    _check_golden("summary_overview", _get(seeded_client, auth, "/api/v1/summary/overview"))


def test_golden_cost(seeded_client: TestClient, auth: dict[str, str]) -> None:
    _check_golden("cost", _get(seeded_client, auth, f"/api/v1/cost?{_WIDE}"))


# --- Structural invariants (inherently now-relative endpoints) ---------------

def test_shape_summary_now(seeded_client: TestClient, auth: dict[str, str]) -> None:
    data = _get(seeded_client, auth, "/api/v1/summary/now")
    assert isinstance(data, dict)
    for key in ("now", "limits", "token_burn_rate_per_min", "today"):
        assert key in data, f"summary/now missing v1 key {key!r}"
    assert isinstance(data["limits"], list)


def test_shape_limits_history(seeded_client: TestClient, auth: dict[str, str]) -> None:
    data = _get(
        seeded_client, auth, "/api/v1/limits/history?window_kind=five_hour&hours=720"
    )
    assert isinstance(data, list)
    for item in data:
        assert {"window_kind", "ts", "utilization_pct"} <= item.keys()


def test_shape_blocks(seeded_client: TestClient, auth: dict[str, str]) -> None:
    data = _get(seeded_client, auth, "/api/v1/blocks?hours=2400")
    assert isinstance(data, list)
    for item in data:
        assert {"start", "end", "total_tokens"} <= item.keys()
