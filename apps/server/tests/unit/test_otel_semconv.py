"""Pinned GenAI semconv mapping constants and doc consistency (Task 71.2)."""

from __future__ import annotations

from pathlib import Path

from tokemetry_server.otel.semconv import (
    SEMCONV_VERSION,
    TOKEN_ATTR_TO_FIELD,
    is_content_attr,
)

_DOC = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "architecture"
    / "otel-mapping.md"
)


def test_semconv_version_pinned_and_documented() -> None:
    assert SEMCONV_VERSION  # a concrete version is pinned
    # The doc pins the same version as the code constant.
    assert SEMCONV_VERSION in _DOC.read_text(encoding="utf-8")


def test_token_map_covers_input_and_output() -> None:
    assert TOKEN_ATTR_TO_FIELD["gen_ai.usage.input_tokens"] == "input_tokens"
    assert TOKEN_ATTR_TO_FIELD["gen_ai.usage.output_tokens"] == "output_tokens"


def test_content_attributes_are_stripped() -> None:
    assert is_content_attr("gen_ai.prompt")
    assert is_content_attr("gen_ai.completion")
    assert is_content_attr("gen_ai.prompt.0.content")  # prefixed body
    assert is_content_attr("gen_ai.input.messages")
    # Non-content usage/metadata attributes are not stripped.
    assert not is_content_attr("gen_ai.usage.input_tokens")
    assert not is_content_attr("gen_ai.request.model")
