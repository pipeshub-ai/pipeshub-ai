"""WP65-SINK: the engine event sink while engine output and gateway calls interleave (seen with Codex).

A command event awaits its presenter (the activity bus) between reserving its batch and adding its lines. A
gateway call's effect commit ``take()``s what is pending in that gap. On ``be391bad20`` the command's lines were then
added to a batch with no sequence, and the next ``flush`` raised ``AssertionError``, ending the engine turn mid-research.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

from harness_core.engines.durable.sink import SEQ_SPAN, JournalEngineEventSink
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    EngineEventMeta,
    ToolCallSeen,
    command_id,
)
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import RecordingPresenter
from harness_core.workspace.types import build_manifest

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import ProgressStamp
    from harness_core.engines.events import EngineEvent
    from harness_core.journal.types import NewEvent
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import WorkspaceManifest

BINDING = RunBinding("o", "s", "r")


@dataclass
class _GatedPresenter(RecordingPresenter):
    """Shows commands (UX5) and saves, each held until the test lets it go: the gap the race needs."""

    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def command(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]:
        self.entered.set()
        await self.release.wait()
        return [self._line(stamp, "command", type(event).__name__)]

    @override
    async def saved(
        self, stamp: ProgressStamp, manifest: WorkspaceManifest, reason: CommitReason
    ) -> list[NewEvent]:
        self.entered.set()
        await self.release.wait()
        return await super().saved(stamp, manifest, reason)


def _sink(
    presenter: RecordingPresenter, commits: list[tuple[str, list[NewEvent]]]
) -> JournalEngineEventSink:
    async def commit(cid: str, events: list[NewEvent]) -> None:
        commits.append((cid, events))

    sink = JournalEngineEventSink("r", "r", presenter, commit)
    sink.begin("trn_x", 3)
    return sink


async def test_wp65_sink_1_a_take_while_a_command_is_presented_loses_and_crashes_nothing() -> (
    None
):
    presenter = _GatedPresenter()
    commits: list[tuple[str, list[NewEvent]]] = []
    sink = _sink(presenter, commits)
    await sink.event(
        BINDING,
        ToolCallSeen(EngineEventMeta("trn_x", 0), "t1", "ph.fetch_url", "started"),
    )
    shown = asyncio.ensure_future(
        sink.event(BINDING, CommandOutput(EngineEventMeta("trn_x", 1), "c1", "line"))
    )
    await presenter.entered.wait()
    carried = await sink.take()
    presenter.release.set()
    await shown
    await sink.flush()
    journaled = [e for _, batch in commits for e in batch]
    assert [e.data["kind"] for e in carried] == ["ToolCallSeen"]
    assert [e.data["kind"] for e in journaled] == ["CommandOutput", "command"], (
        "journaled after the take, not lost"
    )
    ids = [cid for cid, _ in commits]
    assert len(set(ids)) == len(ids) and all(
        cid != command_id("r", "trn_x", 3 * SEQ_SPAN) for cid in ids[1:]
    )


async def test_wp65_sink_2_a_take_while_a_save_is_presented_keeps_its_lines() -> None:
    presenter = _GatedPresenter()
    commits: list[tuple[str, list[NewEvent]]] = []
    sink = _sink(presenter, commits)
    saved = build_manifest(
        "ws:s", revision=1, parent_revision=None, files={}, created_at_ms=0
    )
    saving = asyncio.ensure_future(sink.workspace_saved(BINDING, saved, "autosave"))
    await presenter.entered.wait()
    carried = await sink.take()
    presenter.release.set()
    await saving
    assert [e.type for e in carried] == ["workspace.committed"]
    assert [e.data["kind"] for _, batch in commits for e in batch] == ["version"]


async def test_wp65_sink_3_a_flush_while_a_command_is_presented_keeps_the_batch_whole() -> (
    None
):
    """The flusher (the kernel's timer) cuts the batch in the same gap: the command's lines start a new one."""
    presenter = _GatedPresenter()
    commits: list[tuple[str, list[NewEvent]]] = []
    sink = _sink(presenter, commits)
    await sink.event(
        BINDING,
        ToolCallSeen(EngineEventMeta("trn_x", 0), "t1", "ph.fetch_url", "started"),
    )
    shown = asyncio.ensure_future(
        sink.event(BINDING, CommandStarted(EngineEventMeta("trn_x", 1), "c1", "ls"))
    )
    await presenter.entered.wait()
    await sink.flush()
    presenter.release.set()
    await shown
    await sink.flush()
    kinds = [[e.data["kind"] for e in batch] for _, batch in commits]
    assert kinds == [["ToolCallSeen"], ["CommandStarted", "command"]]
    assert commits[0][0] == command_id("r", "trn_x", 3 * SEQ_SPAN)
    assert len({cid for cid, _ in commits}) == 2


async def test_wp65_sink_4_many_commands_racing_many_takes_journal_every_line_once() -> (
    None
):
    """Parallel ``ph`` calls in a batch script against a stream of command output, interleaved every way at once."""

    class _Yielding(RecordingPresenter):
        async def command(
            self, stamp: ProgressStamp, event: EngineEvent
        ) -> list[NewEvent]:
            await asyncio.sleep(0)
            return [self._line(stamp, "command", f"{event.meta.seq}")]

    commits: list[tuple[str, list[NewEvent]]] = []
    sink = _sink(_Yielding(), commits)
    taken: list[NewEvent] = []

    async def calls() -> None:
        for _ in range(40):
            taken.extend(await sink.take())
            await asyncio.sleep(0)

    async def output() -> None:
        for n in range(120):
            kind = (CommandStarted, CommandOutput, CommandFinished)[n % 3]
            meta = EngineEventMeta("trn_x", n)
            event = (
                CommandFinished(meta, f"c{n}", 0, "completed")
                if kind is CommandFinished
                else kind(meta, f"c{n}", "x")
            )
            await sink.event(BINDING, event)

    _ = await asyncio.gather(calls(), output(), calls())
    await sink.flush()
    lines = [*taken, *(e for _, batch in commits for e in batch)]
    shown = sorted(
        int(str(e.data["text"])) for e in lines if e.data["kind"] == "command"
    )
    assert shown == list(range(120)), "every command's line, once"
    assert len({cid for cid, _ in commits}) == len(commits)
