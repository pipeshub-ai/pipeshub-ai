"""The spill decorator sits on the replay path, so the property that matters
is not "large things went to the object store" -- it is that a step replays to
exactly the value it originally returned, spilled or not.
"""
from __future__ import annotations

import json

import pytest

from app.services.workflows.application.journal_spill import (
    DEFAULT_SPILL_THRESHOLD_BYTES,
    SpillingExecutionJournal,
)
from app.services.workflows.domain.models import (
    ArtifactRef,
    JournalEntry,
    ResultRef,
    StepOutcome,
)


class _InMemoryJournal:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], JournalEntry] = {}
        self.touched: list[str] = []
        self._seq = 0

    async def append(self, entry: JournalEntry) -> None:
        key = (entry.run_id, entry.step_key)
        if key in self.entries:
            return
        self._seq += 1
        self.entries[key] = entry.model_copy(update={"seq": self._seq})

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:
        return self.entries.get((run_id, step_key))

    async def load(self, run_id: str) -> list[JournalEntry]:
        return sorted(
            (e for (rid, _), e in self.entries.items() if rid == run_id),
            key=lambda e: e.seq,
        )

    async def touch(self, run_id: str) -> str | None:
        self.touched.append(run_id)
        return "2099-01-01T00:00:00+00:00" if any(rid == run_id for rid, _ in self.entries) else None

    async def compact(self, run_id: str, upto_seq: int) -> None:
        self.entries = {
            key: entry for key, entry in self.entries.items()
            if not (key[0] == run_id and entry.seq <= upto_seq)
        }


class _InMemoryPayloadStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.puts: list[tuple[str, str, int]] = []

    async def put(self, *, run_id: str, step_key: str, payload: bytes) -> ArtifactRef:
        artifact_id = f"art-{len(self.blobs)}"
        self.blobs[artifact_id] = payload
        self.puts.append((run_id, step_key, len(payload)))
        return ArtifactRef(artifact_id=artifact_id, version="1")

    async def get(self, ref: ArtifactRef) -> bytes | None:
        return self.blobs.get(ref.artifact_id)


class _BrokenPayloadStore:
    async def put(self, *, run_id: str, step_key: str, payload: bytes) -> ArtifactRef:
        raise ConnectionError("object storage unreachable")

    async def get(self, ref: ArtifactRef) -> bytes | None:
        raise ConnectionError("object storage unreachable")


class _LosingPayloadStore(_InMemoryPayloadStore):
    """Accepts writes, then loses them -- an expired or evicted artifact."""

    async def get(self, ref: ArtifactRef) -> bytes | None:
        return None


def _entry(result: object, *, step_key: str = "step-1", run_id: str = "run-1") -> JournalEntry:
    return JournalEntry(
        run_id=run_id,
        seq=0,
        step_key=step_key,
        entry_kind="step",
        idempotency_key=step_key,
        outcome=StepOutcome.SUCCEEDED,
        result_ref=ResultRef(inline=result),
    )


def _big(n: int = DEFAULT_SPILL_THRESHOLD_BYTES * 2) -> str:
    return "x" * n


@pytest.mark.asyncio
class TestSpillThreshold:
    async def test_a_small_result_stays_inline(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads)

        await journal.append(_entry({"ok": True}))

        stored = inner.entries[("run-1", "step-1")]
        assert stored.result_ref.inline == {"ok": True}
        assert stored.result_ref.artifact is None
        assert payloads.puts == []

    async def test_a_large_result_is_spilled_out_of_the_journal(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads)

        await journal.append(_entry({"rows": _big()}))

        stored = inner.entries[("run-1", "step-1")]
        assert stored.result_ref.inline is None
        assert stored.result_ref.artifact is not None
        assert len(payloads.puts) == 1

    async def test_the_threshold_is_configurable(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads, threshold_bytes=8)

        await journal.append(_entry("a somewhat longer string"))

        assert len(payloads.puts) == 1


@pytest.mark.asyncio
class TestReplayFidelity:
    async def test_a_spilled_result_replays_to_the_original_value(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads)
        original = {"rows": [{"id": i, "body": _big(1024)} for i in range(64)]}

        await journal.append(_entry(original))
        replayed = await journal.lookup("run-1", "step-1")

        assert replayed is not None
        assert replayed.result_ref.inline == original

    async def test_load_rehydrates_every_spilled_entry(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads)
        await journal.append(_entry({"a": _big()}, step_key="s1"))
        await journal.append(_entry({"b": 1}, step_key="s2"))
        await journal.append(_entry({"c": _big()}, step_key="s3"))

        entries = await journal.load("run-1")

        assert [e.result_ref.inline for e in entries] == [
            {"a": _big()}, {"b": 1}, {"c": _big()},
        ]

    async def test_a_falsy_large_result_still_round_trips(self) -> None:
        """`inline is None` is the "no result" sentinel, so a spilled empty
        list must come back as `[]`, not None."""
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads, threshold_bytes=1)

        await journal.append(_entry([]))
        replayed = await journal.lookup("run-1", "step-1")

        assert replayed is not None
        assert replayed.result_ref.inline == []


@pytest.mark.asyncio
class TestDegradation:
    async def test_a_failed_spill_keeps_the_result_inline(self) -> None:
        """Losing the step's result would be far worse than a large Redis
        entry, so a broken payload store must not fail the append."""
        inner = _InMemoryJournal()
        journal = SpillingExecutionJournal(inner, _BrokenPayloadStore())

        await journal.append(_entry({"rows": _big()}))

        stored = inner.entries[("run-1", "step-1")]
        assert stored.result_ref.inline == {"rows": _big()}

    async def test_a_lost_payload_does_not_raise_on_replay(self) -> None:
        inner = _InMemoryJournal()
        journal = SpillingExecutionJournal(inner, _LosingPayloadStore())
        await journal.append(_entry({"rows": _big()}))

        replayed = await journal.lookup("run-1", "step-1")

        assert replayed is not None
        assert replayed.result_ref.inline is None
        assert replayed.result_ref.artifact is not None

    async def test_a_non_json_result_is_left_untouched(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads, threshold_bytes=1)
        entry = _entry(object())

        await journal.append(entry)

        assert payloads.puts == []
        assert inner.entries[("run-1", "step-1")].result_ref.inline is entry.result_ref.inline


@pytest.mark.asyncio
class TestPassThrough:
    async def test_an_entry_without_a_result_is_unchanged(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads)
        entry = _entry(None)

        await journal.append(entry)
        replayed = await journal.lookup("run-1", "step-1")

        assert replayed is not None
        assert replayed.result_ref.inline is None
        assert payloads.puts == []

    async def test_compaction_is_delegated(self) -> None:
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads)
        await journal.append(_entry({"a": 1}, step_key="s1"))
        await journal.append(_entry({"b": 2}, step_key="s2"))

        await journal.compact("run-1", upto_seq=1)

        assert [e.step_key for e in await journal.load("run-1")] == ["s2"]

    async def test_the_spilled_bytes_are_the_json_of_the_result(self) -> None:
        """The stored blob has to be exactly what `_rehydrate` parses back,
        with no envelope, or a future reader cannot make sense of it."""
        inner, payloads = _InMemoryJournal(), _InMemoryPayloadStore()
        journal = SpillingExecutionJournal(inner, payloads, threshold_bytes=1)
        value = {"hello": "world"}

        await journal.append(_entry(value))

        (blob,) = payloads.blobs.values()
        assert json.loads(blob.decode()) == value
