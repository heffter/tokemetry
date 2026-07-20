"""Contract-conformance suite: golden batches replayed against the server (Task 65.7).

Replays the versioned conformance set (:mod:`conformance`) through the real
ingest route and pins the server's behavior to it: valid batches must return
their golden counts, invalid batches must return the exact structured error
(content in any extension point rejected, poison index surfaced), and the
committed JSON export must stay in sync -- so a schema change that alters a
golden outcome fails here until the set is versioned forward (PP-010).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from .conformance import (
    CONFORMANCE_VERSION,
    INVALID_CASES,
    InvalidCase,
    build_conformance_document,
)
from .fixtures import ALL_SCENARIOS, Scenario

# apps/server/tests/integration/proxy_harness/ -> repo root is 5 up.
_GOLDEN = (
    Path(__file__).resolve().parents[5]
    / "docs"
    / "integrations"
    / "conformance"
    / f"v{CONFORMANCE_VERSION}"
    / "conformance.json"
)

_VALID_IDS = [s.name for s in ALL_SCENARIOS]
_INVALID_IDS = [c.name for c in INVALID_CASES]


def _ingest(client: TestClient, auth: dict[str, str], events: list[dict[str, Any]]) -> Any:
    return client.post(
        "/api/v2/ingest/events",
        json={"schema_version": 2, "events": events},
        headers=auth,
    )


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=_VALID_IDS)
def test_valid_golden_batch_matches_expected_response(
    scenario: Scenario, client: TestClient, auth: dict[str, str]
) -> None:
    """Each valid golden batch returns its documented ingest counts."""
    response = _ingest(client, auth, scenario.events)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == scenario.accepted, scenario.name
    assert body["updated"] == scenario.updated, scenario.name
    assert body["duplicate"] == scenario.duplicate, scenario.name
    assert body["rejected"] == scenario.rejected, scenario.name
    assert body["corrected"] == 0, scenario.name


@pytest.mark.parametrize("case", INVALID_CASES, ids=_INVALID_IDS)
def test_invalid_golden_batch_returns_exact_errors(
    case: InvalidCase, client: TestClient, auth: dict[str, str]
) -> None:
    """Each invalid golden batch is rejected with its exact structured error."""
    response = _ingest(client, auth, case.events)
    assert response.status_code == 422, response.text
    errors = response.json()["detail"]["errors"]
    for expected in case.expected_errors:
        match = next(
            (
                error
                for error in errors
                if error["index"] == expected["index"] and error["code"] == expected["code"]
            ),
            None,
        )
        assert match is not None, (case.name, expected, errors)
        assert expected["field_path_contains"] in match["field_path"], (case.name, match)


def test_content_is_rejected_in_every_extension_point() -> None:
    """The content-free corpus covers extra, dimensions, and tool_histogram."""
    covered = {case.name for case in INVALID_CASES}
    assert {
        "content_prompt_in_extra",
        "content_key_in_dimensions",
        "tool_histogram_when_disabled",
    } <= covered


def test_poison_event_surfaces_its_batch_index(
    client: TestClient, auth: dict[str, str]
) -> None:
    """A single poison event in a batch surfaces its index (FR-TOK-015)."""
    (case,) = [c for c in INVALID_CASES if c.name == "poison_event_surfaces_its_index"]
    response = _ingest(client, auth, case.events)
    assert response.status_code == 422, response.text
    indices = {error["index"] for error in response.json()["detail"]["errors"]}
    assert indices == {1}  # only the middle event is poison


def test_golden_export_is_in_sync() -> None:
    """The committed JSON export equals the generated set (regenerate on drift)."""
    assert _GOLDEN.exists(), f"missing conformance export: {_GOLDEN}"
    committed = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert committed == build_conformance_document(), (
        "conformance.json is stale; regenerate it and version the set forward"
    )
