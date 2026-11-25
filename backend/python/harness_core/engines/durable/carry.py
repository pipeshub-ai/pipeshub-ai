"""Engine state carried across the runs of one session (E1b, EV2-27).

A message sent after a run ended starts a new run of the session; the user expects the same conversation.
The engine's conversation is its checkpointed state, recorded per run (``engine.checkpointed``), so a run
with no state of its own starts from the state the session's previous engine run of the same person ended
with. Pure: a fold of the session stream's lifecycle events, read alongside the run's own fold.

What is carried, and why:

* the last checkpoint of the most recent root engine run that **completed before this run was admitted**:
  the choice reads only events older than the run, so every attempt of the run makes the same one;
* of the **same principal**: an engine's history holds what its tools read with its actor's access, which
  another participant of a shared session must not inherit;
* **root runs**, and a root loop run's engine children (CTL-6: the loop's ``code_task`` handoffs, whose forks merge
  back into the session's workspace), for those runs only: any other child works on a fork under a narrowed grant;
* of the **same engine kind**: the latest such run decides; when its engine is another kind (or it left no
  state) nothing is carried, rather than an older history that skips it. A version or state schema of the
  same kind the adapter cannot read is refused by the adapter, and the turn starts from the workspace.

The carried run's latest sandbox snapshot (P5.9b) goes with its state, under the same rules.

A ``code_task`` child (CTL-6) journals on its own stream, which holds none of the session's other runs: its
host's admission applies these rules across the session and pins what it carries in the child's spec
(PipesHub: ``x.pipeshub.engineCarry``), read by ``pinned_carry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from harness_core.engines.durable.fold import checkpoint_of, snapshot_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.engines.events import EngineStateBlob
    from harness_core.journal.types import JournalEvent
    from harness_core.workspace.types import ObjectRef


@dataclass
class _SessionRun:
    run_id: str
    admitted_seq: int | None = None
    principal: str | None = None
    parent: str | None = None
    engine_kind: str | None = None
    checkpoint: EngineStateBlob | None = None
    snapshot: ObjectRef | None = None
    snapshot_at: str | None = None
    completed_seq: int | None = None


@dataclass
class SessionEngineRuns:
    """The session's runs as far as carrying engine state goes; ``apply`` every event the run reads."""

    runs: dict[str, _SessionRun] = field(default_factory=dict[str, _SessionRun])

    def apply(self, events: Iterable[JournalEvent]) -> None:
        for event in events:
            if event.run_id is None:
                continue
            run = self.runs.setdefault(event.run_id, _SessionRun(event.run_id))
            data = event.data
            match event.type:
                case "run.admitted":
                    run.admitted_seq = event.seq
                    run.principal = _text(data.get("principalRef"))
                    run.parent = _text(data.get("parentRunId"))
                case "run.spec_pinned":
                    run.engine_kind = _engine_kind(data.get("spec"))
                case "engine.checkpointed":
                    try:
                        run.checkpoint = checkpoint_of(data)
                    except (KeyError, TypeError, ValueError):
                        continue
                case "engine.snapshotted":
                    try:
                        run.snapshot = snapshot_of(data)
                    except (KeyError, TypeError, ValueError):
                        continue
                    run.snapshot_at = event.ts
                case "run.completed":
                    run.completed_seq = event.seq
                case _:
                    pass

    def carried(self, run_id: str, *, principal: str, engine_kind: str) -> tuple[str, EngineStateBlob] | None:
        """The run whose state ``run_id`` starts from, and that state; ``None`` when nothing is carried."""
        me = self.runs.get(run_id)
        if me is None or me.admitted_seq is None or not self._in_conversation(me):
            return None
        principal = me.principal or principal
        admitted = me.admitted_seq
        earlier = [
            r for r in self.runs.values()
            if r.run_id != run_id
            and self._in_conversation(r)
            and r.engine_kind is not None
            and r.principal == principal
            and r.completed_seq is not None
            and r.completed_seq < admitted
        ]
        if not earlier:
            return None
        latest = max(earlier, key=lambda r: cast("int", r.completed_seq))
        state = latest.checkpoint
        if state is None or latest.engine_kind != engine_kind or state.engine.kind != engine_kind:
            return None
        return latest.run_id, state

    def _in_conversation(self, run: _SessionRun) -> bool:
        """A root run, or an engine child a root loop run handed its task to (CTL-6)."""
        if run.parent is None:
            return True
        parent = self.runs.get(run.parent)
        return parent is not None and parent.parent is None and parent.admitted_seq is not None and parent.engine_kind is None

    def snapshot_of(self, run_id: str) -> ObjectRef | None:
        """The latest sandbox snapshot ``run_id`` journaled."""
        run = self.runs.get(run_id)
        return run.snapshot if run is not None else None

    def snapshot_taken_at(self, run_id: str) -> str | None:
        """When ``run_id`` journaled its latest sandbox snapshot (the event's ``ts``)."""
        run = self.runs.get(run_id)
        return run.snapshot_at if run is not None else None


def pinned_carry(spec: RunSpec, *, engine_kind: str) -> tuple[str, EngineStateBlob, ObjectRef | None] | None:
    """The state admission pinned for a ``code_task`` child (PipesHub: ``x.pipeshub.engineCarry``): the run it
    came from, its checkpoint and its snapshot; ``None`` when none is pinned or it is another engine's."""
    extension = (spec.extensions or {}).get("x.pipeshub")
    carry = extension.get("engineCarry") if isinstance(extension, dict) else None
    if not isinstance(carry, dict):
        return None
    try:
        state = checkpoint_of(cast("Mapping[str, JsonValue]", carry["checkpoint"]))
        raw_snapshot = carry.get("snapshot")
        snapshot = snapshot_of(cast("Mapping[str, JsonValue]", raw_snapshot)) if isinstance(raw_snapshot, dict) else None
    except (KeyError, TypeError, ValueError):
        return None
    run_id = _text(carry.get("runId"))
    if run_id is None or state.engine.kind != engine_kind:
        return None
    return run_id, state, snapshot


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _engine_kind(spec: object) -> str | None:
    if not isinstance(spec, dict):
        return None
    executor = cast("Mapping[str, JsonValue]", spec).get("executor")
    if not isinstance(executor, dict) or executor.get("kind") != "engine":
        return None
    engine = executor.get("engine")
    return _text(engine.get("kind")) if isinstance(engine, dict) else None


__all__ = ["SessionEngineRuns", "pinned_carry"]
