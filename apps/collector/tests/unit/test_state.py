"""Unit tests for the collector's SQLite state store."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from tokemetry_collector.state import CollectorState


@pytest.fixture
def state(tmp_path: Path) -> Iterator[CollectorState]:
    with CollectorState(tmp_path / "state.sqlite3") as store:
        yield store


class TestOffsets:
    def test_unseen_file_returns_none(self, state: CollectorState) -> None:
        assert state.get_offset("src", "a.jsonl") is None

    def test_set_and_get(self, state: CollectorState) -> None:
        state.set_offset("src", "a.jsonl", 100, 200)
        offset = state.get_offset("src", "a.jsonl")
        assert offset is not None
        assert offset.offset == 100
        assert offset.size == 200

    def test_update_overwrites(self, state: CollectorState) -> None:
        state.set_offset("src", "a.jsonl", 100, 200)
        state.set_offset("src", "a.jsonl", 300, 400)
        offset = state.get_offset("src", "a.jsonl")
        assert offset is not None
        assert offset.offset == 300


class TestQueue:
    def test_enqueue_and_pending_fifo(self, state: CollectorState) -> None:
        state.enqueue("events", {"n": 1})
        state.enqueue("events", {"n": 2})

        pending = state.pending(10)
        assert [batch.payload["n"] for batch in pending] == [1, 2]
        assert state.queue_size() == 2

    def test_pending_respects_limit(self, state: CollectorState) -> None:
        for index in range(5):
            state.enqueue("events", {"n": index})
        assert len(state.pending(2)) == 2

    def test_mark_uploaded_removes(self, state: CollectorState) -> None:
        batch_id = state.enqueue("events", {"n": 1})
        state.mark_uploaded(batch_id)
        assert state.queue_size() == 0

    def test_bump_attempts(self, state: CollectorState) -> None:
        batch_id = state.enqueue("events", {"n": 1})
        state.bump_attempts(batch_id)
        state.bump_attempts(batch_id)
        pending = state.pending(1)
        assert pending[0].attempts == 2


class TestMeta:
    def test_unset_meta_is_none(self, state: CollectorState) -> None:
        assert state.get_meta("bootstrap_done") is None

    def test_set_and_get_meta(self, state: CollectorState) -> None:
        state.set_meta("bootstrap_done", "1")
        assert state.get_meta("bootstrap_done") == "1"

    def test_persists_across_reopen(self, tmp_path: Path) -> None:
        db = tmp_path / "state.sqlite3"
        with CollectorState(db) as store:
            store.set_offset("src", "a", 10, 20)
            store.enqueue("events", {"n": 1})
        with CollectorState(db) as store:
            assert store.get_offset("src", "a") is not None
            assert store.queue_size() == 1
