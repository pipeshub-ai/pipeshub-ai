"""In-memory adapter specifics: op-log recovery, content verification, checkpoints, live frames."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.journal import (
    Checkpoint,
    CommitRequest,
    ContentMissing,
    InMemoryCheckpointStore,
    InMemoryContentStore,
    InMemoryJournal,
    InMemoryLiveSink,
    JournalUnavailable,
    LiveFrame,
    NewEvent,
    Suspend,
)
from harness_core.testing import ManualClock
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from pathlib import Path


def _admit(journal: InMemoryJournal, run_id: str = "run1") -> None:
    journal.admit(
        run_id,
        org_id="org1",
        session_id="ses1",
        spec=minimal_run_spec(),
        input_text="q",
    )


async def test_oplog_rebuilds_the_journal_in_a_new_process(tmp_path: Path) -> None:
    oplog = tmp_path / "journal.jsonl"
    clock = ManualClock()
    first = InMemoryJournal(clock=clock, oplog=oplog)
    _admit(first)
    claim = await first.claim("run1", worker_id="w1")
    assert claim is not None
    ask = NewEvent(
        "interaction.requested",
        {
            "interactionId": "ix1",
            "kind": "approval",
            "bindingDigest": "sha256:" + "2" * 64,
        },
    )
    _ = await first.commit(
        CommitRequest(
            "c1",
            claim.token,
            (NewEvent("turn.started", {"turn": 1}), ask),
            Suspend("approval", "ix1"),
        )
    )
    first.resolve_interaction("run1", "ix1", "approved")

    second = InMemoryJournal(clock=ManualClock(), oplog=oplog)
    assert second.events("ses1") == first.events("ses1")
    assert second.status("run1") == "queued"
    again = await second.claim("run1", worker_id="w2")
    assert again is not None and again.fence == claim.fence + 1
    replayed = await second.commit(
        CommitRequest(
            "c1",
            claim.token,
            (NewEvent("turn.started", {"turn": 1}), ask),
            Suspend("approval", "ix1"),
        )
    )
    assert replayed.replayed


async def test_rejected_commits_are_not_logged(tmp_path: Path) -> None:
    oplog = tmp_path / "journal.jsonl"
    journal = InMemoryJournal(clock=ManualClock(), oplog=oplog)
    _admit(journal)
    claim = await journal.claim("run1", worker_id="w1")
    assert claim is not None
    lines = oplog.read_text().splitlines()
    with pytest.raises(Exception, match="may not write"):
        _ = await journal.commit(
            CommitRequest(
                "c1", claim.token, (NewEvent("run.completed", {"outcome": "answered"}),)
            )
        )
    assert oplog.read_text().splitlines() == lines


async def test_injected_unavailability_and_stale_reads() -> None:
    journal = InMemoryJournal(clock=ManualClock())
    _admit(journal)
    claim = await journal.claim("run1", worker_id="w1")
    assert claim is not None
    journal.unavailable_calls = 1
    request = CommitRequest("c1", claim.token, (NewEvent("turn.started", {"turn": 1}),))
    with pytest.raises(JournalUnavailable):
        _ = await journal.commit(request)
    receipt = await journal.commit(request)
    journal.stale_reads = 1
    stale = await journal.read("ses1")
    assert stale[-1].seq == receipt.events[0].seq - 1
    assert (await journal.read("ses1"))[-1].seq == receipt.events[0].seq


async def test_content_store_verifies_hash_and_size(tmp_path: Path) -> None:
    store = InMemoryContentStore(tmp_path / "cas")
    ref = await store.put(b"payload")
    assert await store.get(ref) == b"payload"
    assert await InMemoryContentStore(tmp_path / "cas").get(ref) == b"payload"
    with pytest.raises(ContentMissing):
        _ = await store.get(ref.model_copy(update={"size": 3}))
    store.drop(ref)
    with pytest.raises(ContentMissing):
        _ = await store.get(ref)


async def test_checkpoint_store_round_trips(tmp_path: Path) -> None:
    store = InMemoryCheckpointStore(tmp_path / "ckpt")
    checkpoint = Checkpoint(
        run_id="run1", stream_id="ses1", fold_version=1, seq=4, state={"a": [1]}
    )
    ref = await store.save(checkpoint)
    assert await store.load(ref) == checkpoint
    assert await InMemoryCheckpointStore(tmp_path / "ckpt").load(ref) == checkpoint
    assert await store.load("missing") is None


async def test_live_sink_drops_frames_from_an_older_fence() -> None:
    sink = InMemoryLiveSink()
    await sink.publish(LiveFrame("run1", 2, 1, 1, "text_delta", {"text": "a"}))
    await sink.publish(LiveFrame("run1", 1, 1, 2, "text_delta", {"text": "zombie"}))
    assert [f.frame_id for f in sink.frames] == ["d:run1:2:1:1"]
    assert [f.data for f in sink.dropped] == [{"text": "zombie"}]
