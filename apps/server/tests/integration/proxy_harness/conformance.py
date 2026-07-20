"""Versioned contract-conformance set: golden batches and outcomes (Task 65.7).

The proxy team builds against this: a versioned set of golden request/response
pairs the server's behavior is pinned to. Valid batches (reused from the harness
:mod:`fixtures`) carry their exact expected ingest counts; invalid batches carry
the exact structured error the server must return -- content smuggled into every
extension point (``extra``, ``dimensions``, ``tool_histogram``) is rejected
(FR-TOK-029, AC-011), and a poison event in a batch surfaces its index so the
client's recursive splitting terminates (FR-TOK-015).

:func:`build_conformance_document` renders the whole set to a JSON-able dict; it
is exported to ``docs/integrations/conformance/v<version>/conformance.json`` as
the artifact the proxy repository vendors. The conformance test both replays it
against the live server and asserts the committed export is in sync, so any v2
schema change that alters a golden outcome fails CI until the set is versioned
forward (PP-010).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .fixtures import ALL_SCENARIOS, Scenario, _event

#: The conformance-set version. Bump deliberately when a schema change alters a
#: golden outcome (PP-010); the proxy repo pins to a version directory.
CONFORMANCE_VERSION = "2.0"


@dataclass(frozen=True)
class InvalidCase:
    """A batch that must be rejected, with the exact error(s) it must surface.

    Each expected error matches by ``index`` (batch position), ``code``, and a
    substring the ``field_path`` must contain (paths embed keys verbatim).
    """

    name: str
    reason: str
    events: list[dict[str, Any]]
    expected_errors: list[dict[str, Any]]


def _valid(event_id: str, **over: Any) -> dict[str, Any]:
    """A minimal valid anthropic attempt, for perturbing into an invalid case."""
    return _event(
        event_id,
        "anthropic",
        "claude-sonnet-4-5",
        success=True,
        outcome="success",
        input_tokens=10,
        output_tokens=5,
        **over,
    )


# Content smuggled into every extension point must be rejected (FR-TOK-029).
INVALID_CASES: list[InvalidCase] = [
    InvalidCase(
        name="content_prompt_in_extra",
        reason="a prompt under a provider namespace in extra is rejected content",
        events=[_valid("conf:extra_prompt", extra={"anthropic": {"prompt": "user secret"}})],
        expected_errors=[{"index": 0, "code": "content_key", "field_path_contains": "prompt"}],
    ),
    InvalidCase(
        name="content_file_path_in_extra",
        reason="a file path anywhere in extra is rejected content",
        events=[_valid("conf:extra_path", extra={"gateway": {"file_path": "/home/u/secret.py"}})],
        expected_errors=[{"index": 0, "code": "content_key", "field_path_contains": "path"}],
    ),
    InvalidCase(
        name="content_code_snippet_in_extra",
        reason="a code snippet nested in extra is rejected content",
        events=[
            _valid(
                "conf:extra_code",
                extra={"anthropic": {"detail": {"snippet": "def f(): return secret"}}},
            )
        ],
        expected_errors=[{"index": 0, "code": "content_key", "field_path_contains": "snippet"}],
    ),
    InvalidCase(
        name="disallowed_extra_namespace",
        reason="an unknown top-level extra namespace is rejected",
        events=[_valid("conf:extra_ns", extra={"exfil": {"k": "v"}})],
        expected_errors=[
            {"index": 0, "code": "extra_namespace_not_allowed", "field_path_contains": "exfil"}
        ],
    ),
    InvalidCase(
        name="content_key_in_dimensions",
        reason="a non-allowlisted (content-like) dimension key is rejected",
        events=[_valid("conf:dim", dimensions={"prompt_text": "the user asked ..."})],
        expected_errors=[
            {"index": 0, "code": "dimension_not_allowed", "field_path_contains": "prompt_text"}
        ],
    ),
    InvalidCase(
        name="tool_histogram_when_disabled",
        reason="a tool-name histogram is rejected while tool names are disabled",
        events=[_valid("conf:tools", tool_histogram={"run_bash": 3})],
        expected_errors=[
            {"index": 0, "code": "tool_names_disabled", "field_path_contains": "tool_histogram"}
        ],
    ),
    InvalidCase(
        name="poison_event_surfaces_its_index",
        reason="one content-bearing event among valid ones surfaces its batch index",
        events=[
            _valid("conf:poison0"),
            _valid("conf:poison1", extra={"anthropic": {"message": "hi there"}}),
            _valid("conf:poison2"),
        ],
        expected_errors=[{"index": 1, "code": "content_key", "field_path_contains": "message"}],
    ),
]


def _valid_document_entry(scenario: Scenario) -> dict[str, Any]:
    """Render one valid scenario as a golden request/response pair."""
    return {
        "name": scenario.name,
        "provider": scenario.provider,
        "request": {"schema_version": 2, "events": scenario.events},
        "expected_response": {
            "accepted": scenario.accepted,
            "updated": scenario.updated,
            "duplicate": scenario.duplicate,
            "rejected": scenario.rejected,
            "corrected": 0,
        },
    }


def _invalid_document_entry(case: InvalidCase) -> dict[str, Any]:
    """Render one invalid case as a golden request/error pair."""
    return {
        "name": case.name,
        "reason": case.reason,
        "request": {"schema_version": 2, "events": case.events},
        "expected_status": 422,
        "expected_errors": case.expected_errors,
    }


def build_conformance_document() -> dict[str, Any]:
    """The full versioned conformance set as a JSON-able document."""
    return {
        "conformance_version": CONFORMANCE_VERSION,
        "schema_version": 2,
        "endpoint": "POST /api/v2/ingest/events",
        "valid_batches": [_valid_document_entry(s) for s in ALL_SCENARIOS],
        "invalid_batches": [_invalid_document_entry(c) for c in INVALID_CASES],
    }
