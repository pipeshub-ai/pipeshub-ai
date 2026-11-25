"""QA19-6 (ENG-G2, TGW-G5): the production ``EngineEventSink``, ``JournalEngineEventSink``, runs the
``EngineEventSinkContract``: each run's sink is the one the engine kernel hands its attempt, committing through the
attempt's fenced commit to the journal. What the sink kept is read back from the journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

import pytest

from harness_core.engines.durable.sink import JournalEngineEventSink
from harness_core.testing.contracts.engine_controller import EngineEventSinkContract
from harness_core.testing.engines.durable import (
    PROGRESS,
    EngineWorld,
    RecordingPresenter,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.engines.durable.ports import ProgressStamp
    from harness_core.engines.events import EngineEvent, EngineStateBlob
    from harness_core.engines.snapshots import SnapshotReason
    from harness_core.interactions.ports import RunBinding
    from harness_core.journal.types import NewEvent
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import ObjectRef, WorkspaceManifest


class _KeyedPresenter(RecordingPresenter):
    """Shows every engine event with its ``(turn, seq)``, so the journal says which events reached it."""

    @override
    def event(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]:
        return [self._line(stamp, "engine", type(event).__name__, {"turnId": event.meta.turn_id, "seq": event.meta.seq})]


@dataclass
class _JournalSinks:
    """One ``JournalEngineEventSink`` per run, as the kernel builds one per attempt."""

    world: EngineWorld = field(default_factory=lambda: EngineWorld(presenter=_KeyedPresenter()))
    sinks: dict[str, JournalEngineEventSink] = field(default_factory=dict[str, JournalEngineEventSink])
    sent: dict[tuple[str, str, int], EngineEvent] = field(default_factory=dict[tuple[str, str, int], "EngineEvent"])
    fences: dict[str, int] = field(default_factory=dict[str, int])

    async def _for(self, run: RunBinding, turn_id: str) -> JournalEngineEventSink:
        if run.run_id not in self.sinks:
            self.world.admit(run.run_id, "contract", session=run.scope)
            attempt = await self.world.engine_attempt(run.run_id)
            assert isinstance(attempt.sink, JournalEngineEventSink)
            self.sinks[run.run_id] = attempt.sink
            self.fences[run.run_id] = attempt.claim.fence
        sink = self.sinks[run.run_id]
        if sink.turn is None or sink.turn.turn_id != turn_id:
            sink.begin(turn_id, self.fences[run.run_id])
        return sink

    @property
    def sink(self) -> _JournalSinks:
        return self

    async def event(self, run: RunBinding, event: EngineEvent) -> None:
        self.sent.setdefault((run.run_id, event.meta.turn_id, event.meta.seq), event)
        await (await self._for(run, event.meta.turn_id)).event(run, event)

    async def workspace_saved(self, run: RunBinding, manifest: WorkspaceManifest, reason: CommitReason) -> None:
        sink = self.sinks.get(run.run_id) or await self._for(run, "turn-save")
        await sink.workspace_saved(run, manifest, reason)

    async def checkpointed(self, run: RunBinding, blob: EngineStateBlob) -> None:
        await self.sinks[run.run_id].checkpointed(run, blob)

    async def snapshotted(self, run: RunBinding, snapshot: ObjectRef, reason: SnapshotReason) -> None:
        await self.sinks[run.run_id].snapshotted(run, snapshot, reason)

    async def recorded(self, run: RunBinding) -> Sequence[EngineEvent]:
        await self.sinks[run.run_id].flush()
        lines = [e.data for e in self.world.events(run.run_id, run.scope) if e.type == PROGRESS]
        return [
            self.sent[(run.run_id, str(line["data"]["turnId"]), int(line["data"]["seq"]))]  # type: ignore[index, arg-type]
            for line in lines
            if line.get("kind") == "engine"  # type: ignore[union-attr]
        ]

    async def saves(self, run: RunBinding) -> Sequence[int]:
        await self.sinks[run.run_id].flush()
        return [
            int(e.data["revision"])  # type: ignore[index, arg-type]
            for e in self.world.events(run.run_id, run.scope)
            if e.type == "workspace.committed"
        ]


class TestJournalEngineEventSink(EngineEventSinkContract):
    @pytest.fixture
    def sink_env(self) -> _JournalSinks:
        return _JournalSinks()
