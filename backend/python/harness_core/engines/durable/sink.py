"""``JournalEngineEventSink``: an engine turn's events, saves and checkpoints on the run's journal (E1).

What a user sees move (commands, output, files, tools, helpers, versions) is
journaled as the host presents it (``EngineRunPresenter``), in batches. Text and
reasoning deltas are not journaled: the turn's final text is, at its end, in
the kernel's turn commit. Each save adds ``workspace.committed``. The checkpoint
is held for the kernel's turn commit, so a turn is complete in the journal with
its state or not at all. A sandbox snapshot (P5.9b) is journaled when it is
stored, so a crash after an autosave still finds it.

A batch's command id is ``command_id(run, turn_id, seq)`` with ``seq`` a number
the batch reserves when it opens: ``fence * SEQ_SPAN + n``. A commit retried
after a lost ack replays under the same id; a later attempt (a higher fence)
never sends an id an earlier one used, which Node would refuse (its payload hash
covers the attempt), and never writes again what an earlier one journaled.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.engines.durable.ports import (
    AsksQuestions,
    KeepsNative,
    ProgressStamp,
    ShowsCommands,
    ShowsToolResults,
)
from harness_core.engines.events import (
    CommandFinished,
    CommandOutput,
    CommandStarted,
    FileChanged,
    NativeUnknown,
    ReasoningDelta,
    SignInProgress,
    TextDelta,
    ToolCallSeen,
    TurnCompleted,
    command_id,
)
from harness_core.engines.native import MAX_UNKNOWN_EVENTS
from harness_core.failpoints import afailpoint
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.types import NewEvent
from harness_core.workspace.changes import diff_files
from harness_core.workspace.events import workspace_committed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.contracts.generated.common import ContentRef
    from harness_core.engines.durable.ports import EngineRunPresenter
    from harness_core.engines.events import EngineEvent, EngineStateBlob
    from harness_core.engines.native import TranscriptPart
    from harness_core.engines.snapshots import SnapshotReason
    from harness_core.gateway.asking import AskedQuestion
    from harness_core.gateway.types import McpResult
    from harness_core.interactions.ports import RunBinding
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import ObjectRef, WorkspaceFile, WorkspaceManifest

    Commit = Callable[[str, list[NewEvent]], Awaitable[object]]
    # Bytes kept out of their ref, in the run's content store (erased with the conversation).
    Seal = Callable[[bytes], Awaitable[ContentRef]]
    ParentFiles = Callable[[WorkspaceManifest], Awaitable["Mapping[str, WorkspaceFile] | None"]]

SNAPSHOTTED = "engine.snapshotted"
# A fence's own range of sequence numbers within a turn.
SEQ_SPAN = 1 << 32
FLUSH_EVENTS = 32
# A step starting or ending is journaled at once: batched, a whole turn's steps reached the chat only at its end.
_STEP_EVENTS = (SignInProgress, CommandStarted, CommandFinished, ToolCallSeen, FileChanged)


def _journaled_at_once(event: EngineEvent) -> bool:
    """A gateway call's start is the exception: its call's intent, a moment later, carries it (``take``), so the
    call does not wait on a commit of its own for it (PF-05); a call that never reaches the gateway leaves it to the
    next batch."""
    if isinstance(event, ToolCallSeen):
        return event.status != "started"
    return isinstance(event, _STEP_EVENTS)


@dataclass
class TurnBinding:
    """The turn the sink writes for: its id and the attempt's fence."""

    turn_id: str
    fence: int
    _n: int = 0

    def allocate(self) -> int:
        seq = self.fence * SEQ_SPAN + self._n
        self._n += 1
        return seq


async def _no_parent(manifest: WorkspaceManifest) -> Mapping[str, WorkspaceFile] | None:
    return None


@dataclass
class JournalEngineEventSink:
    """``EngineEventSink`` for one attempt; ``begin`` names the turn before the controller drives it."""

    run_id: str
    task_id: str
    presenter: EngineRunPresenter
    commit: Commit
    parent_files: ParentFiles = _no_parent
    flush_events: int = FLUSH_EVENTS
    # Where a native message or transcript is kept (C2.39, C2.40); unset, or a presenter that keeps none: not kept.
    seal: Seal | None = None
    turn: TurnBinding | None = None
    checkpoint: EngineStateBlob | None = None
    _pending: list[NewEvent] = field(default_factory=list[NewEvent])
    _first: int | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last: dict[str, WorkspaceManifest] = field(default_factory=dict[str, "WorkspaceManifest"])
    _seen: set[tuple[str, int]] = field(default_factory=set[tuple[str, int]])
    _unknown_kept: int = 0

    def begin(self, turn_id: str, fence: int) -> None:
        self.turn = TurnBinding(turn_id, fence)
        self.checkpoint = None
        self._unknown_kept = 0

    def stamp(self) -> ProgressStamp:
        turn = self._turn()
        return ProgressStamp(self.task_id, turn.turn_id, turn.allocate)

    def _turn(self) -> TurnBinding:
        if self.turn is None:
            raise RuntimeError("the sink has no turn: begin() before driving one")
        return self.turn

    def _open(self) -> None:
        """Reserves the open batch's number before its lines are stamped: its command id is the first of the batch."""
        if self._first is None:
            self._first = self._turn().allocate()

    def _add(self, events: Sequence[NewEvent]) -> None:
        """Adds lines to the open batch, opening one when there is none. Synchronous, as ``take`` and ``flush``'s cut
        are, so a batch and its number change together and never across an await. Presenting a line can await (the
        activity bus, a seal, a save's diff), and a gateway call's ``take`` or the flusher may cut the batch then:
        the lines go to a new batch with a number of its own (never to a batch with none)."""
        if not events:
            return
        self._open()
        self._pending.extend(events)

    async def event(self, run: RunBinding, event: EngineEvent) -> None:
        if isinstance(event, (TextDelta, ReasoningDelta, TurnCompleted)):
            return
        # The port drops an event already recorded (QA-19 ES-2): a re-delivered one would be journaled twice.
        seen = (event.meta.turn_id, event.meta.seq)
        if seen in self._seen:
            return
        self._seen.add(seen)
        if isinstance(event, NativeUnknown):
            await self._native_unknown(event)
            return
        self._open()
        shown = self.presenter.event(self.stamp(), event)
        if isinstance(event, (CommandStarted, CommandOutput, CommandFinished)) and isinstance(
            self.presenter, ShowsCommands
        ):
            shown = [*shown, *await self.presenter.command(self.stamp(), event)]
        # The await a presenter's command record can take (the activity bus's log write); WP65-SINK-D1 widens it.
        await afailpoint("engine.sink.presented")
        if not shown:
            return
        self._add(shown)
        if len(self._pending) >= self.flush_events or _journaled_at_once(event):
            await self.flush()

    async def _native_unknown(self, event: NativeUnknown) -> None:
        """EV-13: kept sealed, at most ``MAX_UNKNOWN_EVENTS`` a turn (the controller counts every one)."""
        if self.seal is None or not isinstance(self.presenter, KeepsNative) or self._unknown_kept >= MAX_UNKNOWN_EVENTS:
            return
        self._unknown_kept += 1
        content = await self.seal(event.raw)
        self._open()
        self._add(self.presenter.native_unknown(self.stamp(), event, content))
        if len(self._pending) >= self.flush_events:
            await self.flush()

    async def native_transcript(self, run: RunBinding, part: TranscriptPart) -> None:
        """C2.39: a part of the turn's native transcript, sealed; journaled with the turn's next batch."""
        if self.seal is None or not isinstance(self.presenter, KeepsNative):
            return
        content = await self.seal(part.data)
        self._open()
        self._add(self.presenter.native_transcript(self.stamp(), part, content))
        if len(self._pending) >= self.flush_events or part.final:
            await self.flush()

    async def tool_result(self, tool: str, arguments: Mapping[str, JsonValue], result: McpResult) -> None:
        """What a gateway call returned, journaled with its step when the presenter shows results: in the batch the
        step's end opens (the engine's "completed" event follows its answer, and a step's end is journaled at once),
        never as a commit of its own the engine's answer waits on (PF-05: a commit is the gateway's slowest part)."""
        if self.turn is None or not isinstance(self.presenter, ShowsToolResults):
            return
        self._open()
        shown = self.presenter.tool_result(self.stamp(), tool, arguments, result)
        if not shown:
            return
        self._add(shown)
        if len(self._pending) >= self.flush_events:
            await self.flush()

    async def question(self, asked: AskedQuestion) -> None:
        """The question an engine asked (``ask_user``), journaled as the chat's question card with its step."""
        if self.turn is None or not isinstance(self.presenter, AsksQuestions):
            return
        self._open()
        shown = self.presenter.question(self.stamp(), asked)
        if not shown:
            return
        self._add(shown)
        await self.flush()

    async def workspace_saved(self, run: RunBinding, manifest: WorkspaceManifest, reason: CommitReason) -> None:
        self._open()
        parent = self._last.get(manifest.workspace_id)
        before = parent.files if parent is not None and parent.revision == manifest.parent_revision else None
        if before is None and manifest.parent_revision is not None:
            before = await self.parent_files(manifest)
        diff = diff_files(before or {}, manifest.files)
        self._last[manifest.workspace_id] = manifest
        self._add([workspace_committed(manifest.workspace_id, manifest.revision, sorted(diff.paths), diff.kinds())])
        self._add(await self.presenter.saved(self.stamp(), manifest, reason))
        await self.flush()

    async def checkpointed(self, run: RunBinding, blob: EngineStateBlob) -> None:
        self.checkpoint = blob

    async def snapshotted(self, run: RunBinding, snapshot: ObjectRef, reason: SnapshotReason) -> None:
        self._add([
            NewEvent(
                SNAPSHOTTED,
                {
                    "snapshot": {
                        "objectId": snapshot.object_id,
                        "contentHash": snapshot.content_hash,
                        "sizeBytes": snapshot.size_bytes,
                    },
                    "turnId": self._turn().turn_id,
                    "reason": reason,
                },
                critical=False,
            )
        ])
        await self.flush()

    async def take(self) -> list[NewEvent]:
        """What is pending, for a commit of the attempt's own to carry ahead of its events (a gateway call's intent
        carries the lines shown before it, in the order they were shown)."""
        async with self._lock:
            events, self._pending, self._first = self._pending, [], None
            return events

    async def flush(self) -> None:
        """Commit what is pending, in batches the journal accepts (one save can show more files than a commit
        holds); a failed commit propagates (the attempt ends)."""
        async with self._lock:
            if not self._pending:
                return
            # Every line came through ``_add``, which opens a batch: pending lines always have their number.
            assert self._first is not None
            events, first = self._pending, self._first
            self._pending, self._first = [], None
            turn = self._turn()
            for start in range(0, len(events), MAX_COMMIT_EVENTS):
                seq = first if start == 0 else turn.allocate()
                await self.commit(command_id(self.run_id, turn.turn_id, seq), events[start : start + MAX_COMMIT_EVENTS])


__all__ = ["FLUSH_EVENTS", "SEQ_SPAN", "SNAPSHOTTED", "JournalEngineEventSink", "TurnBinding"]
