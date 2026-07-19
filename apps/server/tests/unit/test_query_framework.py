"""Unit tests for the v2 query framework's pure helpers (Task 66.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from tokemetry_server.services.query_framework import (
    Grain,
    build_page,
    decode_cursor,
    encode_cursor,
    enforce_range_bound,
    parse_grain,
    parse_sort,
    truncate_to_grain,
)

_ALLOWED = frozenset({"ts", "cost", "tokens"})


def test_parse_sort_default_and_direction() -> None:
    assert parse_sort(None, _ALLOWED, "-ts") == parse_sort("-ts", _ALLOWED, "ts")
    asc = parse_sort("cost", _ALLOWED, "-ts")
    assert asc.field == "cost" and asc.descending is False
    desc = parse_sort("-cost", _ALLOWED, "-ts")
    assert desc.field == "cost" and desc.descending is True


def test_parse_sort_rejects_unknown_field() -> None:
    with pytest.raises(ValueError, match="not one of"):
        parse_sort("bogus", _ALLOWED, "-ts")


def test_parse_grain_and_default() -> None:
    assert parse_grain(None) is Grain.DAY
    assert parse_grain("week") is Grain.WEEK
    with pytest.raises(ValueError, match="not one of"):
        parse_grain("hour")


def test_truncate_to_grain() -> None:
    wednesday = date(2026, 7, 15)  # a Wednesday
    assert truncate_to_grain(wednesday, Grain.DAY) == wednesday
    assert truncate_to_grain(wednesday, Grain.WEEK) == date(2026, 7, 13)  # Monday
    assert truncate_to_grain(wednesday, Grain.MONTH) == date(2026, 7, 1)


def test_enforce_range_bound() -> None:
    start = datetime(2026, 7, 1, tzinfo=UTC)
    enforce_range_bound(start, start + timedelta(days=30), max_days=31)  # ok
    with pytest.raises(ValueError, match="exceeds"):
        enforce_range_bound(start, start + timedelta(days=40), max_days=31)
    with pytest.raises(ValueError, match="must not precede"):
        enforce_range_bound(start, start - timedelta(days=1), max_days=31)


def test_cursor_round_trips_various_types() -> None:
    for value in ("2026-07-01T00:00:00+00:00", 42, "anthropic:req_1"):
        assert decode_cursor(encode_cursor(value, 7)) == (value, 7)
    # date/datetime are stored as ISO strings.
    stamp = datetime(2026, 7, 1, 12, tzinfo=UTC)
    assert decode_cursor(encode_cursor(stamp, 3)) == (stamp.isoformat(), 3)


def test_decode_cursor_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="invalid pagination cursor"):
        decode_cursor("not-base64!!")
    with pytest.raises(ValueError, match="invalid pagination cursor"):
        decode_cursor(encode_cursor("x", 1)[:-2])


def test_build_page_detects_next_from_extra_row() -> None:
    # limit+1 rows -> a next page whose cursor comes from the last kept item.
    page = build_page(["a", "b", "c"], limit=2, cursor_of=lambda x: f"cur:{x}")
    assert page.items == ["a", "b"] and page.next_cursor == "cur:b"
    # Exactly limit rows -> last page.
    last = build_page(["a", "b"], limit=2, cursor_of=lambda x: f"cur:{x}")
    assert last.items == ["a", "b"] and last.next_cursor is None
    assert build_page([], limit=2, cursor_of=lambda x: "cur").next_cursor is None
