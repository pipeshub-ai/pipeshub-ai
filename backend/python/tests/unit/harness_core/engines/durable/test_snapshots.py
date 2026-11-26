"""KS-1..KS-4 (P5.9b): sandbox snapshots on the durable engine path (in-memory journal, fake broker)."""

from __future__ import annotations

import dataclasses
import logging

import pytest

from harness_core.engines.durable.fold import fold_engine_run
from harness_core.engines.snapshots import SNAPSHOT_FOREIGN, SnapshotPolicy
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.engines.durable import (
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
)

POLICY = SnapshotPolicy(("/tmp/.cache",), max_bytes=1024 * 1024)
CACHE = "/tmp/.cache/tool/index.json"

class Watching(ScriptedEngineAdapter):
    """Records the sandbox's files when each engine session opens."""

    def __init__(self) -> None:
        super().__init__()
        self.files_at_open: list[dict[str, bytes]] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.files_at_open.append(dict(sandbox.files))
        return await super().open(sandbox, launch, restore, correlation=correlation)


def world() -> tuple[EngineWorld, Watching]:
    adapter = Watching()
    return EngineWorld(adapter=adapter, snapshots=POLICY), adapter


async def finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


async def test_ks1_each_stored_snapshot_is_journaled_and_folded() -> None:
    w, _ = world()
    w.admit("run-a", f"write {CACHE} warm\nsay ok")
    assert await finish(w, "run-a") == "completed"
    [event] = [e for e in w.events("run-a") if e.type == "engine.snapshotted"]
    assert (event.critical, event.writer, event.data["reason"]) == (False, "worker", "turn_end")
    state = fold_engine_run(w.journal.events(SESSION), run_id="run-a")
    assert state.snapshot is not None and state.snapshot.object_id == event.data["snapshot"]["objectId"]  # type: ignore[index]
    assert w.types("run-a").index("engine.snapshotted") < w.types("run-a").index("engine.checkpointed")


async def test_ks1_snapshots_off_journal_nothing() -> None:
    w = EngineWorld()
    w.admit("run-a", f"write {CACHE} warm\nsay ok")
    assert await finish(w, "run-a") == "completed"
    assert "engine.snapshotted" not in w.types("run-a")


async def test_ks2_a_turn_redriven_after_an_autosave_snapshot_restores_it() -> None:
    w, adapter = world()
    w.config = dataclasses.replace(w.config, autosave_changes=2)
    w.admit("run-a", "write /tmp/.cache/a 1\nwrite /tmp/.cache/b 2\nsay ok")
    assert await w.crash_at("run-a", "engine.turn.after_snapshot")
    assert [e.data["reason"] for e in w.events("run-a") if e.type == "engine.snapshotted"] == ["autosave"]
    assert await finish(w, "run-a") == "completed"
    first, redo = adapter.files_at_open[0], adapter.files_at_open[-1]
    assert "/tmp/.cache/a" not in first
    assert (redo.get("/tmp/.cache/a"), redo.get("/tmp/.cache/b")) == (b"1", b"2")


async def test_ks3_the_next_run_of_the_same_person_starts_from_the_carried_runs_snapshot() -> None:
    w, adapter = world()
    w.admit("run-a", f"write {CACHE} warm\nsay ok")
    assert await finish(w, "run-a") == "completed"
    w.admit("run-b", "say again")
    assert await finish(w, "run-b") == "completed"
    assert adapter.files_at_open[-1].get(CACHE) == b"warm"
    w.admit("run-c", "say theirs", principal="p_other")
    assert await finish(w, "run-c") == "completed"
    assert CACHE not in adapter.files_at_open[-1]


@pytest.mark.journal_history_unchecked("injects an engine event into another session with no run")
async def test_ks4_a_ref_naming_another_sessions_snapshot_is_never_restored(caplog: pytest.LogCaptureFixture) -> None:
    w, adapter = world()
    w.admit("run-a", f"write {CACHE} theirs\nsay ok")
    assert await finish(w, "run-a") == "completed"
    [theirs] = [e for e in w.events("run-a") if e.type == "engine.snapshotted"]
    w.admit("run-x", "say mine", session="ses-other")
    _ = w.journal.inject(
        "ses-other",
        {"type": "engine.snapshotted", "runId": "run-x", "writer": "worker", "critical": False, "data": theirs.data},
    )
    with caplog.at_level(logging.WARNING):
        assert await finish(w, "run-x") == "completed"
    assert CACHE not in adapter.files_at_open[-1]
    unusable = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.snapshot.unusable"]
    assert [r.harness["reasonCode"] for r in unusable] == [SNAPSHOT_FOREIGN]  # type: ignore[attr-defined]
