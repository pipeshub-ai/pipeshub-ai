"""UX5 EG-1, EG-2, EG-3, RD-5: an engine run's shell commands and plan, through the presenter and the journal sink."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.engines.presenter import PipesHubEngineRunPresenter
from harness_core.activity import SECRET_MASK, BoundedCommandActivity
from harness_core.engines.durable.sink import JournalEngineEventSink
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineEventMeta,
    PlanItem,
    PlanUpdated,
)
from harness_core.journal.memory import InMemoryContentStore, InMemoryLiveBus
from harness_core.testing.clock import ManualClock

if TYPE_CHECKING:
    from harness_core.engines.events import EngineEvent
    from harness_core.journal.types import NewEvent

SECRET = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"


class _World:
    def __init__(self, *, journal_commands: bool = True) -> None:
        self.bus = InMemoryLiveBus()
        self.clock = ManualClock()
        self.activity = BoundedCommandActivity(
            self.bus, run_id="run-1", attempt_id="att-1", fence=2, clock=self.clock, content=InMemoryContentStore()
        )
        self.presenter = PipesHubEngineRunPresenter(commands=self.activity, journal_commands=journal_commands)
        self.committed: list[NewEvent] = []
        self.sink = JournalEngineEventSink("run-1", "run-1", self.presenter, self._commit)
        self.sink.begin("trn_1", 2)
        self._n = 0

    async def _commit(self, command_id: str, events: list[NewEvent]) -> None:
        self.committed.extend(events)

    def meta(self) -> EngineEventMeta:
        self._n += 1
        return EngineEventMeta("trn_1", self._n)

    async def feed(self, *events: EngineEvent) -> None:
        for event in events:
            await self.sink.event(None, event)  # type: ignore[arg-type]
        await self.sink.flush()
        await self.activity.drained()

    def of(self, kind: str) -> list[NewEvent]:
        return [e for e in self.committed if e.type == kind]


async def test_eg1_codex_streams_each_delta_live_and_journals_the_record() -> None:
    w = _World()
    await w.feed(CommandStarted(w.meta(), "call_1", "pytest -q"))
    for chunk in ("collected 3\n", "..", "F\n"):
        await w.feed(CommandOutput(w.meta(), "call_1", chunk))
    _ = w.clock.advance(2.5)
    await w.feed(CommandFinished(w.meta(), "call_1", 1, "failed"))
    frames = await w.bus.read("run-1")
    assert [f.kind for f in frames] == ["command_started", "command_output", "command_output", "command_finished"]
    assert frames[0].data["source"] == "engine"
    assert frames[0].data["taskId"] == "run-1"
    [record] = w.of("x.pipeshub.command.finished")
    assert (record.data["commandId"], record.data["exitCode"], record.data["status"]) == ("call_1", 1, "failed")
    assert record.data["durationMs"] == 2500
    assert record.data["stdout"] == {"tail": "collected 3\n..F\n", "bytes": 16, "truncated": False}
    lines = [e.data["kind"] for e in w.of("x.pipeshub.engine.progress")]
    assert lines[0] == "command"
    assert lines[-1] == "command_done"


async def test_eg2_output_only_at_completion_is_one_frame() -> None:
    w = _World()
    await w.feed(
        CommandStarted(w.meta(), "tu_1", "ls"),
        CommandOutput(w.meta(), "tu_1", "a\nb\n"),
        CommandOutput(w.meta(), "tu_1", "warn\n", "stderr"),
        CommandFinished(w.meta(), "tu_1", 0, "completed"),
    )
    frames = await w.bus.read("run-1")
    assert [(f.kind, f.data.get("stream")) for f in frames] == [
        ("command_started", None),
        ("command_output", "stdout"),
        ("command_output", "stderr"),
        ("command_finished", None),
    ]
    [record] = w.of("x.pipeshub.command.finished")
    assert record.data["stderr"]["tail"] == "warn\n"  # pyright: ignore[reportIndexIssue,reportCallIssue,reportArgumentType]


async def test_rd5_engine_progress_lines_and_records_are_redacted() -> None:
    w = _World()
    half = len(SECRET) // 2
    await w.feed(
        CommandStarted(w.meta(), "c1", f"curl -H 'token: {SECRET}' x"),
        CommandOutput(w.meta(), "c1", f"key={SECRET[:half]}"),
        CommandOutput(w.meta(), "c1", f"{SECRET[half:]}\n"),
        CommandFinished(w.meta(), "c1", 0, "completed"),
    )
    journaled = str([e.data for e in w.committed])
    assert SECRET not in journaled
    assert SECRET[:half] not in journaled
    assert SECRET_MASK in journaled
    live = str([f.data for f in await w.bus.read("run-1")])
    assert SECRET[:half] not in live


async def test_eg3_a_plan_becomes_task_events_only_for_what_changed() -> None:
    w = _World()
    first = (PlanItem("Read", "in_progress"), PlanItem("Fix", "pending"))
    await w.feed(PlanUpdated(w.meta(), first))
    await w.feed(PlanUpdated(w.meta(), (PlanItem("Read", "completed"), PlanItem("Fix", "pending"), PlanItem("Test", "pending"))))
    assert [(e.type, e.data["taskId"], e.data["status"]) for e in w.committed] == [
        ("task.created", "task-1", "in_progress"),
        ("task.created", "task-2", "pending"),
        ("task.updated", "task-1", "completed"),
        ("task.created", "task-3", "pending"),
    ]


async def test_b1_6_an_item_the_engine_drops_is_cancelled() -> None:
    w = _World()
    await w.feed(PlanUpdated(w.meta(), (PlanItem("Read", "completed"), PlanItem("Fix", "pending"))))
    await w.feed(PlanUpdated(w.meta(), (PlanItem("Read", "completed"),)))
    assert [(e.type, e.data["taskId"], e.data["status"]) for e in w.committed][-1] == ("task.updated", "task-2", "cancelled")


async def test_rc5_engine_an_older_node_gets_no_record() -> None:
    w = _World(journal_commands=False)
    await w.feed(CommandStarted(w.meta(), "c1", "ls"), CommandFinished(w.meta(), "c1", 0, "completed"))
    assert w.of("x.pipeshub.command.finished") == []
    assert [f.kind for f in await w.bus.read("run-1")][-1] == "command_finished"
