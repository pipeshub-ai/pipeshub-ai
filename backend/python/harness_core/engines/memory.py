"""In-memory engine ports: ``EngineEventSink`` (what an engine turn reported, in order, deduplicated by
command id) and ``EngineCeilingSource`` (org ceilings as stored wire documents)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.engines.errors import ProfileCeilingUnavailable
from harness_core.engines.events import command_id
from harness_core.engines.profiles import constraint_from_wire

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.engines.events import EngineEvent, EngineStateBlob
    from harness_core.engines.snapshots import SnapshotReason
    from harness_core.engines.types import ProfileConstraint
    from harness_core.interactions.ports import RunBinding
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import ObjectRef, WorkspaceManifest


@dataclass(frozen=True, slots=True)
class SavedRecord:
    run: RunBinding
    manifest: WorkspaceManifest
    reason: CommitReason


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    run: RunBinding
    blob: EngineStateBlob


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    run: RunBinding
    snapshot: ObjectRef
    reason: SnapshotReason


@dataclass
class InMemoryEngineEventSink:
    events: list[tuple[RunBinding, EngineEvent]] = field(default_factory=list[tuple["RunBinding", "EngineEvent"]])
    saves: list[SavedRecord] = field(default_factory=list[SavedRecord])
    checkpoints: list[CheckpointRecord] = field(default_factory=list[CheckpointRecord])
    snapshots: list[SnapshotRecord] = field(default_factory=list[SnapshotRecord])
    _seen: set[str] = field(default_factory=set[str])

    async def event(self, run: RunBinding, event: EngineEvent) -> None:
        key = command_id(run.run_id, event.meta.turn_id, event.meta.seq)
        if key in self._seen:
            return
        self._seen.add(key)
        self.events.append((run, event))

    async def workspace_saved(self, run: RunBinding, manifest: WorkspaceManifest, reason: CommitReason) -> None:
        self.saves.append(SavedRecord(run, manifest, reason))

    async def checkpointed(self, run: RunBinding, blob: EngineStateBlob) -> None:
        self.checkpoints.append(CheckpointRecord(run, blob))

    async def snapshotted(self, run: RunBinding, snapshot: ObjectRef, reason: SnapshotReason) -> None:
        self.snapshots.append(SnapshotRecord(run, snapshot, reason))

    def of_run(self, run_id: str) -> list[EngineEvent]:
        return [event for run, event in self.events if run.run_id == run_id]


@dataclass
class InMemoryEngineCeilings:
    """Org ceilings kept as the wire documents an admin stored; parsed on every read, like the real store."""

    stored: dict[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])
    unreadable: bool = False

    def store(self, org_id: str, ceiling: JsonValue) -> None:
        if ceiling is None:
            _ = self.stored.pop(org_id, None)
        else:
            self.stored[org_id] = ceiling

    async def ceiling(self, org_id: str) -> ProfileConstraint | None:
        if self.unreadable:
            raise ProfileCeilingUnavailable("the org ceiling could not be read")
        raw = self.stored.get(org_id)
        return None if raw is None else constraint_from_wire(raw)



__all__ = [
    "CheckpointRecord",
    "InMemoryEngineCeilings",
    "InMemoryEngineEventSink",
    "SavedRecord",
    "SnapshotRecord",
]
