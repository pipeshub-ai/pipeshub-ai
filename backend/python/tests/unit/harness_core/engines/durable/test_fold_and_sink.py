"""CC-3, JS-1..JS-5 (E1): the engine run's fold, older folds over it, and the journal sink."""

from __future__ import annotations

import pytest

from harness_core.contracts.generated import event_catalog
from harness_core.engines.durable.fold import fold_engine_run
from harness_core.engines.durable.kernel import parent_reader
from harness_core.engines.durable.sink import (
    SEQ_SPAN,
    JournalEngineEventSink,
    TurnBinding,
)
from harness_core.engines.events import (
    CommandOutput,
    CommandStarted,
    EngineEventMeta,
    ReasoningDelta,
    TextDelta,
    TurnCompleted,
    command_id,
)
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import CommandConflict, CommitRequest, NewEvent
from harness_core.kernel import fold as loop_fold
from harness_core.logs import HarnessLog
from harness_core.testing.engines.durable import (
    PROGRESS,
    SESSION,
    EngineWorld,
    RecordingPresenter,
)
from harness_core.workspace.memory import InMemoryWorkspaceStore
from harness_core.workspace.types import (
    ObjectScope,
    WorkspaceManifest,
    WorkspaceStoreUnavailable,
    build_manifest,
)

RUN = "run-fs"
META = EngineEventMeta("trn_x", 0)


@pytest.fixture
def world() -> EngineWorld:
    return EngineWorld()


async def test_cc3_loop_folds_read_a_session_holding_an_engine_run(world: EngineWorld) -> None:
    world.admit(RUN, 'tool lookup {"q": "a"}\nwrite notes.md hi\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    events = world.journal.events(SESSION)
    session = loop_fold.fold_session(events, stream_id=SESSION)
    run = loop_fold.fold_run(events, run_id=RUN)
    assert run.status == "completed"
    # The engine's invocation ids are not the loop's (`t<turn>.<i>`): they attach to no session entry.
    assert all(not entry.results for entry in session.entries)
    assert [e.role for e in session.entries if e.run_id == RUN] == ["user", "assistant"]


async def test_cc3_an_older_worker_ignores_the_engine_events(world: EngineWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    world.admit(RUN, "write notes.md hi\nsay done")
    assert (await world.attempt(RUN)).status == "completed"
    older = {k: v for k, v in event_catalog.EVENT_MODELS.items() if k != "engine.checkpointed"}
    monkeypatch.setattr(loop_fold, "EVENT_MODELS", older)
    loop_fold._data_model.cache_clear()  # pyright: ignore[reportPrivateUsage]
    try:
        events = world.journal.events(SESSION)
        session = loop_fold.fold_session(events, stream_id=SESSION)
        assert session.ignored_events >= 1
        assert [e for e in events if e.type == "engine.checkpointed" and e.critical] == []
        assert [e for e in events if e.type == PROGRESS and e.critical] == []
    finally:
        monkeypatch.undo()
        loop_fold._data_model.cache_clear()  # pyright: ignore[reportPrivateUsage]


async def test_fold_reads_turns_effects_checkpoint_and_workspace(world: EngineWorld) -> None:
    world.admit(RUN, 'write out/a.md hi\ntool create {"name": "x"}\nsay done')
    world.approvals = {"create": "allow"}
    assert (await world.attempt(RUN)).status == "completed"
    state = fold_engine_run(world.journal.events(SESSION), run_id=RUN)
    assert [(t.index, t.completed) for t in state.turns] == [(1, True)]
    assert state.checkpoint is not None and state.checkpoint_turn is not None
    assert [i.state for i in state.invocations.values()] == ["completed"]
    assert "/out/a.md" in state.changed_paths
    assert state.status == "completed"


async def test_js1_deltas_and_turn_ends_are_not_journaled() -> None:
    committed: list[list[NewEvent]] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        committed.append(events)

    presenter = RecordingPresenter()
    sink = JournalEngineEventSink("r", "r", presenter, commit)
    sink.begin("trn_x", 3)
    binding = RunBinding("o", "s", "r")
    for event in (TextDelta(META, "m", "hi"), ReasoningDelta(META, "m", "hm"), TurnCompleted(META, "completed", "x")):
        await sink.event(binding, event)
    await sink.flush()
    assert committed == [] and presenter.calls == []
    await sink.event(binding, CommandStarted(META, "c1", "ls"))
    await sink.flush()
    assert [[e.type for e in batch] for batch in committed] == [[PROGRESS]]


async def test_js2_js3_batch_ids_are_deterministic_within_an_attempt_and_never_reused_across_fences() -> None:
    ids: list[str] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        ids.append(cid)

    for fence in (3, 3, 4):
        sink = JournalEngineEventSink("r", "r", RecordingPresenter(), commit)
        sink.begin("trn_x", fence)
        await sink.event(RunBinding("o", "s", "r"), CommandStarted(META, "c1", "ls"))
        await sink.flush()
    assert ids[0] == ids[1] == command_id("r", "trn_x", 3 * SEQ_SPAN)
    assert ids[2] == command_id("r", "trn_x", 4 * SEQ_SPAN)
    assert TurnBinding("t", 2).allocate() == 2 * SEQ_SPAN


async def test_js3_the_journal_refuses_a_command_id_reused_by_another_attempt(world: EngineWorld) -> None:
    world.admit(RUN, "say hi")
    first = await world.journal.claim(RUN, worker_id="w1")
    assert first is not None
    event = NewEvent(PROGRESS, {"k": 1}, critical=False)
    _ = await world.journal.commit(CommitRequest("cmd_same", first.token, (event,)))
    world.expire()
    second = await world.journal.claim(RUN, worker_id="w2")
    assert second is not None
    with pytest.raises(CommandConflict):
        _ = await world.journal.commit(CommitRequest("cmd_same", second.token, (event,)))


async def test_js2_a_lost_ack_replays_the_batch_once(world: EngineWorld) -> None:
    world.admit(RUN, "shell echo a\nshell echo b\nsay done")
    world.journal.lost_acks = 2
    assert (await world.attempt(RUN)).status == "completed"
    progress = [e for e in world.events(RUN) if e.type == PROGRESS]
    keys = [(e.data["turnId"], e.data["seq"]) for e in progress]
    assert len(keys) == len(set(keys))


async def test_js4_a_batch_is_flushed_at_its_size_bound() -> None:
    batches: list[int] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        batches.append(len(events))

    sink = JournalEngineEventSink("r", "r", RecordingPresenter(), commit, flush_events=4)
    sink.begin("trn_x", 1)
    for i in range(9):
        await sink.event(RunBinding("o", "s", "r"), CommandOutput(EngineEventMeta("trn_x", i), "c1", f"line {i}\n"))
    await sink.flush()
    assert batches == [4, 4, 1]


async def test_ctresults_a_step_starting_or_ending_reaches_the_journal_at_once() -> None:
    """A whole turn's steps were journaled in one batch at its end, so a running code task showed none."""
    batches: list[list[str]] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        batches.append([str(e.data["kind"]) for e in events])

    sink = JournalEngineEventSink("r", "r", RecordingPresenter(), commit)
    sink.begin("trn_x", 1)
    await sink.event(RunBinding("o", "s", "r"), CommandStarted(EngineEventMeta("trn_x", 0), "c1", "pytest -q"))
    assert batches == [["CommandStarted"]]
    await sink.event(RunBinding("o", "s", "r"), CommandOutput(EngineEventMeta("trn_x", 1), "c1", "ok\n"))
    assert len(batches) == 1


async def test_ctresults_a_gateway_call_journals_what_it_returned(world: EngineWorld) -> None:
    """An engine's PipesHub tool call showed as started only; its result never reached the chat."""
    world.admit(RUN, 'tool lookup {"q": "churn"}\nsay done')
    assert (await world.attempt(RUN)).status == "completed"
    shown = [e.data for e in world.events(RUN) if e.type == PROGRESS and e.data["kind"] == "tool_result"]
    assert len(shown) == 1 and str(shown[0]["text"]).endswith("lookup")
    assert "lookup done" in str(shown[0]["data"]["text"])  # type: ignore[index]


async def test_js5_each_save_journals_what_changed(world: EngineWorld) -> None:
    world.admit(RUN, "write a.md one\nwrite b.md two\nsay done")
    assert (await world.attempt(RUN)).status == "completed"
    commits = [e.data for e in world.events(RUN) if e.type == "workspace.committed"]
    assert commits and commits[-1]["changed"] == ["/a.md", "/b.md"]
    assert all(str(p).startswith("/") for c in commits for p in c["changed"])  # type: ignore[union-attr]


class _RevisionsUnreachable(InMemoryWorkspaceStore):
    async def revision(self, scope: ObjectScope, workspace_id: str, revision: int) -> WorkspaceManifest:
        raise WorkspaceStoreUnavailable("workspace store unreachable")


async def test_m5_1_a_parent_the_store_cannot_read_fails_the_save_instead_of_journaling_every_file_changed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    committed: list[list[NewEvent]] = []

    async def commit(cid: str, events: list[NewEvent]) -> None:
        committed.append(events)

    sink = JournalEngineEventSink("r", "r", RecordingPresenter(), commit)
    sink.parent_files = parent_reader(_RevisionsUnreachable(), "o", HarnessLog.named("test", runId="r"))
    sink.begin("trn_x", 3)
    saved = build_manifest("ws:s", revision=2, parent_revision=1, files={}, created_at_ms=0)

    with pytest.raises(WorkspaceStoreUnavailable):
        await sink.workspace_saved(RunBinding("o", "s", "r"), saved, "autosave")
    await sink.flush()

    assert all(e.type != "workspace.committed" for batch in committed for e in batch)
    [line] = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.workspace.parent_unreadable"]
    assert line.harness["errorType"] == "WorkspaceStoreUnavailable"  # type: ignore[attr-defined]
