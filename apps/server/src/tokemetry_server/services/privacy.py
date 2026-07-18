"""Privacy and safety validation for v2 usage events (Epic TOK-3).

Tokemetry stores no content: no prompt, completion, tool argument, file path,
code snippet, or reasoning text ever enters the system (FR-EVENT-021,
FR-PRIV-001/002). The v2 wire model has no field to hold such data, but the
free-form ``extra`` map, the ``dimensions`` map, and the optional tool-name
histogram are open enough to smuggle it. This module is the strict server-side
gate that closes those gaps before an event is persisted (FR-EVENT-022).

The checks and the container each guards:

- ``extra`` -- the only free-form, arbitrarily nested container. A recursive
  scan rejects (or, under ``strip`` policy, removes) any key that looks like
  content, and top-level keys are bounded to a provider/gateway namespace.
- ``dimensions`` -- an allowlist (default ``team``, ``cost_center``,
  ``environment``, D-004) plus key-count and key/value length bounds
  (FR-EVENT-020). A non-allowlisted key is always rejected, so a content-like
  dimension key cannot appear regardless of privacy mode.
- ``tool_histogram`` -- gated by the ``tool_names_enabled`` server setting
  (D-005, default off): when disabled any histogram is rejected; when enabled
  it is bounded by name count, name length, and non-negative integer counts.
  Tool names are permitted content when enabled, so the content-key scan does
  not apply to them.
- ``routing`` -- a fixed-schema model (``extra=forbid``) with known-safe field
  names, so it needs no key scan.
- The whole event -- a maximum serialized byte size (FR-EVENT-028) and a JSON
  nesting-depth limit (NFR-SEC-004).

Violations are returned as structured :class:`ValidationIssue` records
(``field_path``, ``code``, ``message``) so the v2 ingest error format can add
the batch event index (FR-INGEST-006). Settings-to-policy wiring lives with the
ingest endpoints (task 62.6); :class:`PrivacyPolicy` carries the defaults.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from tokemetry_core.usage_v2 import UsageEventV2

#: Normalized substrings that mark a key as content-bearing. Matching is on the
#: alphanumeric-only, lowercased key, so ``file_path``, ``inputText``, and
#: ``reasoning_content`` all match via ``path``/``text``/``content``. The scan
#: intentionally over-rejects (a key merely containing one of these is enough):
#: leaking content is far costlier than a source renaming a benign field.
_CONTENT_KEY_TOKENS = frozenset(
    {
        "prompt",
        "response",
        "message",
        "content",
        "text",
        "arguments",
        "path",
        "code",
        "snippet",
        "completion",
        "body",
    }
)


def _normalize_key(key: object) -> str:
    """Lowercase a key and drop non-alphanumerics for token matching."""
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _looks_like_content(key: object) -> bool:
    """Whether ``key`` contains any content-bearing token."""
    normalized = _normalize_key(key)
    return any(token in normalized for token in _CONTENT_KEY_TOKENS)


def _json_depth(value: Any) -> int:
    """Maximum container-nesting depth of a JSON-shaped value.

    Scalars are depth 0; each nested mapping or sequence adds one. Strings and
    bytes are scalars, never iterated.
    """
    if isinstance(value, Mapping):
        return 1 + max((_json_depth(v) for v in value.values()), default=0)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return 1 + max((_json_depth(v) for v in value), default=0)
    return 0


@dataclass(frozen=True)
class ValidationIssue:
    """One structured validation failure (FR-INGEST-006).

    ``field_path`` is a dotted path into the event (``extra.anthropic.prompt``,
    ``dimensions.secret``, ``tool_histogram``, or ``<event>`` for whole-event
    rules). The batch layer prefixes the event index at ingest time.
    """

    field_path: str
    code: str
    message: str


@dataclass(frozen=True)
class PrivacyPolicy:
    """Configurable privacy limits; defaults match PRD decisions D-004/D-005."""

    #: ``reject`` refuses events with content-like keys; ``strip`` removes them.
    mode: str = "reject"
    #: Dimension keys permitted at all (D-004).
    dimension_allowlist: frozenset[str] = frozenset(
        {"team", "cost_center", "environment"}
    )
    #: Maximum number of dimension entries (FR-EVENT-020).
    max_dimensions: int = 16
    #: Maximum dimension key length (FR-EVENT-020).
    max_key_length: int = 64
    #: Maximum dimension value length (FR-EVENT-020).
    max_value_length: int = 256
    #: Whether a tool-name histogram is accepted at all (D-005, default off).
    tool_names_enabled: bool = False
    #: Maximum distinct tool names when the histogram is enabled.
    max_tool_names: int = 32
    #: Maximum tool-name length when the histogram is enabled.
    max_tool_name_length: int = 64
    #: Extra top-level namespaces allowed in addition to the event's provider.
    extra_namespaces: frozenset[str] = frozenset({"gateway"})
    #: Maximum serialized event size in bytes (FR-EVENT-028).
    max_event_bytes: int = 32 * 1024
    #: Maximum JSON nesting depth of the whole event (NFR-SEC-004).
    max_json_depth: int = 8


@dataclass(frozen=True)
class PrivacyResult:
    """Outcome of validating one event.

    ``event`` is the original event, or -- under ``strip`` mode -- a copy with
    content-like keys removed. ``issues`` are fatal violations; a non-empty
    tuple means the event must be rejected. ``stripped`` lists the field paths
    removed under ``strip`` mode (informational, never fatal).
    """

    event: UsageEventV2
    issues: tuple[ValidationIssue, ...] = ()
    stripped: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the event passed with no fatal issue."""
        return not self.issues


class PrivacyValidator:
    """Applies the strict content-free policy to v2 usage events."""

    def __init__(self, policy: PrivacyPolicy | None = None) -> None:
        """Create the validator with ``policy`` (defaults when omitted)."""
        self._policy = policy or PrivacyPolicy()

    @property
    def policy(self) -> PrivacyPolicy:
        """The active policy."""
        return self._policy

    def issues(self, event: UsageEventV2) -> list[ValidationIssue]:
        """Return fatal issues for ``event`` without transforming it.

        Used by the validation-only endpoint (FR-INGEST-007), which reports
        problems but never persists or strips.
        """
        return list(self.sanitize(event).issues)

    def sanitize(self, event: UsageEventV2) -> PrivacyResult:
        """Validate ``event``; strip or reject content-like keys per policy."""
        issues: list[ValidationIssue] = []
        stripped: list[str] = []

        self._check_size_and_depth(event, issues)
        cleaned_extra = self._check_extra(event, issues, stripped)
        self._check_dimensions(event, issues)
        self._check_tool_histogram(event, issues)

        result_event = event
        if self._policy.mode == "strip" and stripped:
            result_event = replace_extra(event, cleaned_extra)

        return PrivacyResult(
            event=result_event, issues=tuple(issues), stripped=tuple(stripped)
        )

    def _check_size_and_depth(
        self, event: UsageEventV2, issues: list[ValidationIssue]
    ) -> None:
        """Enforce the maximum serialized size and JSON nesting depth."""
        size = len(event.model_dump_json().encode("utf-8"))
        if size > self._policy.max_event_bytes:
            issues.append(
                ValidationIssue(
                    "<event>",
                    "event_too_large",
                    f"serialized event is {size} bytes, over the "
                    f"{self._policy.max_event_bytes} byte limit",
                )
            )
        depth = _json_depth(event.model_dump(mode="json"))
        if depth > self._policy.max_json_depth:
            issues.append(
                ValidationIssue(
                    "<event>",
                    "event_too_deep",
                    f"event nests {depth} levels, over the "
                    f"{self._policy.max_json_depth} level limit",
                )
            )

    def _check_extra(
        self,
        event: UsageEventV2,
        issues: list[ValidationIssue],
        stripped: list[str],
    ) -> dict[str, Any]:
        """Scan ``extra`` for content keys and bound its top-level namespaces.

        Returns the cleaned ``extra`` map (content keys removed); the caller
        applies it only under ``strip`` mode.
        """
        allowed_namespaces = {event.provider} | self._policy.extra_namespaces
        for namespace in event.extra:
            if namespace not in allowed_namespaces:
                issues.append(
                    ValidationIssue(
                        f"extra.{namespace}",
                        "extra_namespace_not_allowed",
                        f"extra namespace {namespace!r} is not the event "
                        "provider or an allowed gateway namespace",
                    )
                )

        hits: list[str] = []
        # ``extra`` is always a mapping, so the recursive scan yields a dict.
        cleaned = cast("dict[str, Any]", self._scan_content_keys(event.extra, "extra", hits))
        for path in hits:
            if self._policy.mode == "strip":
                stripped.append(path)
            else:
                issues.append(
                    ValidationIssue(
                        path,
                        "content_key",
                        f"key at {path} looks like content and is prohibited",
                    )
                )
        return cleaned

    def _scan_content_keys(
        self, value: Any, path: str, hits: list[str]
    ) -> Any:
        """Recursively collect content-key paths and return a cleaned copy."""
        if isinstance(value, Mapping):
            cleaned: dict[str, Any] = {}
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if _looks_like_content(key):
                    hits.append(child_path)
                    continue
                cleaned[str(key)] = self._scan_content_keys(child, child_path, hits)
            return cleaned
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [
                self._scan_content_keys(item, f"{path}[{index}]", hits)
                for index, item in enumerate(value)
            ]
        return value

    def _check_dimensions(
        self, event: UsageEventV2, issues: list[ValidationIssue]
    ) -> None:
        """Enforce the dimension allowlist and count/length bounds (D-004)."""
        dimensions = event.dimensions
        if len(dimensions) > self._policy.max_dimensions:
            issues.append(
                ValidationIssue(
                    "dimensions",
                    "too_many_dimensions",
                    f"{len(dimensions)} dimensions exceed the "
                    f"{self._policy.max_dimensions} limit",
                )
            )
        for key, value in dimensions.items():
            if key not in self._policy.dimension_allowlist:
                issues.append(
                    ValidationIssue(
                        f"dimensions.{key}",
                        "dimension_not_allowed",
                        f"dimension key {key!r} is not in the allowlist",
                    )
                )
            if len(key) > self._policy.max_key_length:
                issues.append(
                    ValidationIssue(
                        f"dimensions.{key}",
                        "dimension_key_too_long",
                        f"dimension key exceeds {self._policy.max_key_length} chars",
                    )
                )
            if len(value) > self._policy.max_value_length:
                issues.append(
                    ValidationIssue(
                        f"dimensions.{key}",
                        "dimension_value_too_long",
                        f"dimension value exceeds {self._policy.max_value_length} chars",
                    )
                )

    def _check_tool_histogram(
        self, event: UsageEventV2, issues: list[ValidationIssue]
    ) -> None:
        """Gate and bound the optional tool-name histogram (D-005)."""
        histogram = event.tool_histogram
        if histogram is None:
            return
        if not self._policy.tool_names_enabled:
            issues.append(
                ValidationIssue(
                    "tool_histogram",
                    "tool_names_disabled",
                    "tool-name histogram is disabled by server policy",
                )
            )
            return
        if len(histogram) > self._policy.max_tool_names:
            issues.append(
                ValidationIssue(
                    "tool_histogram",
                    "too_many_tool_names",
                    f"{len(histogram)} tool names exceed the "
                    f"{self._policy.max_tool_names} limit",
                )
            )
        for name, count in histogram.items():
            if len(name) > self._policy.max_tool_name_length:
                issues.append(
                    ValidationIssue(
                        f"tool_histogram.{name}",
                        "tool_name_too_long",
                        f"tool name exceeds {self._policy.max_tool_name_length} chars",
                    )
                )
            if count < 0:
                issues.append(
                    ValidationIssue(
                        f"tool_histogram.{name}",
                        "tool_count_negative",
                        "tool count must be non-negative",
                    )
                )


def replace_extra(event: UsageEventV2, extra: dict[str, Any]) -> UsageEventV2:
    """Return a copy of ``event`` with its ``extra`` map replaced.

    Used by ``strip`` mode to drop content-like keys. The replacement is a
    subset of the already-valid ``extra`` map, so no re-validation is needed.
    """
    return event.model_copy(update={"extra": extra})
