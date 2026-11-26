"""The fold of an engine run's journal (E1): what the next attempt needs to resume it.

An engine run keeps the loop's lifecycle events (``run.*``, ``input.*``,
``interaction.*``, ``child.*``) and adds its own reading of the rest: which
turns started and completed and with what prompt, the gateway tool calls as
invocations (``tool.*`` from the run's ``JournalEffectLog``: engine invocation
ids are allocated per call, so unlike the loop's they are created by their
intent), the last ``engine.checkpointed``, ``engine.snapshotted`` and
``workspace.committed``, and the interactions a turn deferred (``loop.state_saved`` slot ``engine.deferred``).

Pure and total: an event it does not read is skipped. ``fold_run``/``fold_session``
are unchanged, so a loop worker never needs this fold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from harness_core.contracts.generated.common import ContentRef
from harness_core.engines.durable.inputs import (
    SkippedInput,
    pinned_input,
    skipped_input,
)
from harness_core.engines.events import EngineStateBlob
from harness_core.engines.repository_checkpoint import RepositoryCommit
from harness_core.engines.types import EngineInput, EngineVersion
from harness_core.interactions.journal import said_in
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.kernel.fold import Invocation
from harness_core.sandbox.types import RuntimeMode
from harness_core.workspace.types import ObjectRef

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent, RunStatus

# Where a turn records the approvals it deferred: the next turn tells the engine their decisions.
DEFERRED_SLOT = "engine.deferred"
# Bounded so the summary a resumed engine reads stays short.
_SUMMARY_ITEMS = 20


@dataclass
class TurnRecord:
    index: int
    prompt_ref: ContentRef | None = None
    # Steering inputs this turn took as its prompt (``input.consumed`` in its start commit).
    inputs: list[int] = field(default_factory=list[int])
    completed: bool = False
    response_ref: ContentRef | None = None
    started_seq: int = 0
    # Paths the turn's journaled saves changed: what a re-driven turn is told its interrupted attempt did.
    saved: list[str] = field(default_factory=list[str])
    # The input artifacts the turn was given, as pinned in its ``turn.started`` (E1b).
    pins: list[EngineInput] = field(default_factory=list[EngineInput])
    # Those it refused on its own and went on without (R36COVER-FU-3), as pinned beside them.
    skipped: list[SkippedInput] = field(default_factory=list[SkippedInput])


@dataclass
class ChildState:
    child_run_id: str
    invocation_id: str | None = None
    completed: bool = False
    outcome: str | None = None
    error_type: str | None = None
    result_ref: ContentRef | None = None


@dataclass
class EngineRunState:
    run_id: str
    seq: int = 0
    status: RunStatus = "queued"
    outcome: str | None = None
    cancel_requested: bool = False
    suspension: str | None = None
    input_recorded: bool = False
    turns: list[TurnRecord] = field(default_factory=list[TurnRecord])
    invocations: dict[str, Invocation] = field(default_factory=dict[str, Invocation])
    interactions: dict[str, str | None] = field(default_factory=dict[str, "str | None"])
    # What the person said with a decision: a denial's reason, a question's answer.
    said: dict[str, str] = field(default_factory=dict[str, str])
    queued: dict[int, ContentRef | None] = field(default_factory=dict[int, "ContentRef | None"])
    consumed: list[int] = field(default_factory=list[int])
    deferred: dict[int, tuple[str, ...]] = field(default_factory=dict[int, tuple[str, ...]])
    checkpoint: EngineStateBlob | None = None
    checkpoint_turn: str | None = None
    # The run's latest sandbox snapshot (P5.9b): what its next attempt restores.
    snapshot: ObjectRef | None = None
    # When that snapshot was journaled (its event's ``ts``): past the run's snapshot TTL it is not restored (OWD-12).
    snapshot_at: str | None = None
    workspace_revisions: dict[str, int] = field(default_factory=dict[str, int])
    changed_paths: list[str] = field(default_factory=list[str])
    tools_called: list[str] = field(default_factory=list[str])
    children: dict[str, ChildState] = field(default_factory=dict[str, ChildState])
    # A turn's start commit journals its prompt (or the inputs it consumes) before `turn.started` (G4, F-5).
    next_prompt: ContentRef | None = None
    next_inputs: list[int] = field(default_factory=list[int])

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def last_turn(self) -> TurnRecord | None:
        return self.turns[-1] if self.turns else None

    @property
    def queued_inputs(self) -> list[int]:
        return [seq for seq in self.queued if seq not in self.consumed]

    def decision(self, interaction_id: str) -> str | None:
        return self.interactions.get(interaction_id)

    def undecided(self, interaction_ids: Iterable[str]) -> list[str]:
        return [ix for ix in interaction_ids if self.interactions.get(ix) is None]


def fold_engine_run(
    events: Iterable[JournalEvent], *, run_id: str, state: EngineRunState | None = None
) -> EngineRunState:
    out = state if state is not None else EngineRunState(run_id=run_id)
    for event in events:
        if event.seq <= out.seq:
            continue
        out.seq = event.seq
        if event.run_id != run_id:
            continue
        try:
            _apply(out, event)
        except (KeyError, TypeError, ValueError, ValidationError):
            # A payload this fold cannot read is not this run's to repair; Node validated it on write.
            continue
    return out


def _ref(value: object) -> ContentRef | None:
    return ContentRef.model_validate(value) if isinstance(value, dict) else None


def _apply(state: EngineRunState, event: JournalEvent) -> None:
    data = event.data
    match event.type:
        case "run.claimed":
            state.status = "running"
        case "run.suspended":
            state.status, state.suspension = "suspended", str(data.get("reason"))
        case "run.resumed" | "run.yielded":
            state.status, state.suspension = "queued", None
        case "run.cancel_requested":
            state.cancel_requested = True
        case "run.completed":
            state.status, state.outcome = "completed", str(data.get("outcome"))
        case "run.failed":
            state.status, state.outcome = "failed", str(data.get("errorType"))
        case "run.cancelled":
            state.status = "cancelled"
        case "run.quarantined":
            state.status = "quarantined"
        case "turn.started":
            index = int(cast("int", data["turn"]))
            if not any(t.index == index for t in state.turns):
                state.turns.append(
                    TurnRecord(
                        index,
                        prompt_ref=state.next_prompt,
                        inputs=list(state.next_inputs),
                        started_seq=event.seq,
                        pins=[pinned_input(cast("Mapping[str, JsonValue]", pin)) for pin in cast("list[object]", data.get("inputs") or [])],
                        skipped=[
                            skipped_input(cast("Mapping[str, JsonValue]", item))
                            for item in cast("list[object]", data.get("skippedInputs") or [])
                        ],
                    )
                )
            state.next_prompt, state.next_inputs = None, []
        case "turn.completed":
            for turn in state.turns:
                if turn.index == int(cast("int", data["turn"])):
                    turn.completed = True
        case "message.completed":
            _message(state, data)
        case "model.response":
            turn = state.last_turn
            if turn is not None and turn.response_ref is None:
                turn.response_ref = _ref(data.get("messageRef"))
        case "input.queued":
            state.queued.setdefault(int(cast("int", data["messageSeq"])), _ref(data.get("textRef")))
        case "input.consumed":
            seq = int(cast("int", data["messageSeq"]))
            if seq not in state.consumed:
                state.consumed.append(seq)
            turn = state.last_turn
            if turn is not None and not turn.completed:
                turn.inputs.append(seq)
            else:
                state.next_inputs.append(seq)
        case "tool.intent" | "tool.denied" | "tool.outcome" | "tool.unknown":
            _tool(state, event)
        case "interaction.requested":
            _ = state.interactions.setdefault(str(data["interactionId"]), None)
        case "interaction.resolved":
            ix = str(data["interactionId"])
            state.interactions[ix] = str(data["decision"])
            said = _said(data.get("answerRef"))
            if said is not None:
                state.said[ix] = said
        case "loop.state_saved":
            _slots(state, cast("Mapping[str, JsonValue]", data.get("slots") or {}))
        case "engine.checkpointed":
            state.checkpoint = checkpoint_of(data)
            state.checkpoint_turn = str(data["turnId"])
        case "engine.snapshotted":
            state.snapshot = snapshot_of(data)
            state.snapshot_at = event.ts
        case "workspace.committed":
            workspace = str(data["workspaceId"])
            state.workspace_revisions[workspace] = max(
                state.workspace_revisions.get(workspace, 0), int(cast("int", data["revision"]))
            )
            turn = state.last_turn
            for path in cast("list[str]", data.get("changed") or []):
                _remember(state.changed_paths, path)
                if turn is not None and not turn.completed and path not in turn.saved:
                    turn.saved.append(path)
        case "child.spawned":
            child = str(data["childRunId"])
            invocation = data.get("invocationId")
            state.children.setdefault(child, ChildState(child, str(invocation) if invocation else None))
        case "child.completed":
            record = state.children.setdefault(str(data["childRunId"]), ChildState(str(data["childRunId"])))
            record.completed = True
            record.outcome = cast("str | None", data.get("outcome"))
            record.error_type = cast("str | None", data.get("errorType"))
            record.result_ref = _ref(data.get("resultRef"))
        case _:
            pass


def _message(state: EngineRunState, data: Mapping[str, JsonValue]) -> None:
    if data.get("role") != "user":
        return
    state.input_recorded = True
    turn = state.last_turn
    if turn is not None and not turn.completed:
        if turn.prompt_ref is None:
            turn.prompt_ref = _ref(data.get("textRef"))
    else:
        state.next_prompt = _ref(data.get("textRef"))


def _tool(state: EngineRunState, event: JournalEvent) -> None:
    data = event.data
    invocation_id = str(data["invocationId"])
    inv = state.invocations.get(invocation_id)
    if inv is None:
        turn = state.last_turn.index if state.last_turn is not None else 0
        inv = Invocation(
            invocation_id=invocation_id,
            turn=turn,
            index=len(state.invocations),
            call_id=invocation_id,
            name=str(data.get("toolId") or ""),
            args_digest=str(data.get("argsDigest") or ""),
        )
        state.invocations[invocation_id] = inv
    if inv.state == "completed":
        return
    match event.type:
        case "tool.intent":
            inv.tool_id = str(data["toolId"])
            inv.effect = str(data["effect"])
            inv.args_digest = str(data["argsDigest"])
            inv.intent_attempt_id = event.attempt_id
            inv.state = "intent"
            _remember(state.tools_called, inv.tool_id)
        case "tool.denied":
            inv.denied_rule = str(data["rule"])
            if isinstance(data.get("toolId"), str) and inv.tool_id is None:
                inv.tool_id = str(data["toolId"])
        case "tool.unknown":
            inv.state = "unknown"
        case _:
            inv.state = "completed"
            inv.status = str(data["status"])
            inv.result_ref = _ref(data.get("resultRef"))
            receipt = data.get("receipt")
            inv.receipt = cast("dict[str, JsonValue]", receipt) if isinstance(receipt, dict) else None


def _slots(state: EngineRunState, slots: Mapping[str, JsonValue]) -> None:
    deferred = slots.get(DEFERRED_SLOT)
    if not isinstance(deferred, dict):
        return
    for turn, ids in deferred.items():
        if isinstance(ids, list):
            state.deferred[int(turn)] = tuple(str(ix) for ix in ids)


def checkpoint_data(blob: EngineStateBlob, turn_id: str) -> dict[str, JsonValue]:
    """An ``engine.checkpointed`` event's data for ``blob``, captured at ``turn_id``'s end: ``checkpoint_of``'s inverse."""
    data: dict[str, JsonValue] = {
        "blob": {
            "objectId": blob.content.object_id,
            "contentHash": blob.content.content_hash,
            "sizeBytes": blob.content.size_bytes,
        },
        "engine": {
            "kind": blob.engine.kind,
            "version": blob.engine.version,
            "binaryDigest": blob.engine.binary_digest,
        },
        "schemaVersion": blob.schema_version,
        "turnId": turn_id,
    }
    if blob.repository is not None:
        data["repository"] = cast("JsonValue", blob.repository.wire())
    if blob.isolation is not None:
        data["isolation"] = blob.isolation.value
    return data


def checkpoint_of(data: Mapping[str, JsonValue]) -> EngineStateBlob:
    """The state blob an ``engine.checkpointed`` event names."""
    blob = cast("dict[str, JsonValue]", data["blob"])
    engine = cast("dict[str, str]", data["engine"])
    return EngineStateBlob(
        engine=EngineVersion(kind=engine["kind"], version=engine["version"], binary_digest=engine["binaryDigest"]),  # type: ignore[arg-type]
        schema_version=int(cast("int", data["schemaVersion"])),
        content=ObjectRef(str(blob["objectId"]), str(blob["contentHash"]), int(cast("int", blob["sizeBytes"]))),
        repository=RepositoryCommit.from_wire(data.get("repository")),
        isolation=RuntimeMode(isolation) if isinstance(isolation := data.get("isolation"), str) else None,
    )


def snapshot_of(data: Mapping[str, JsonValue]) -> ObjectRef:
    """The sandbox snapshot an ``engine.snapshotted`` event names."""
    ref = cast("dict[str, JsonValue]", data["snapshot"])
    return ObjectRef(str(ref["objectId"]), str(ref["contentHash"]), int(cast("int", ref["sizeBytes"])))


def _remember(items: list[str], item: str) -> None:
    if item in items:
        items.remove(item)
    items.append(item)
    del items[:-_SUMMARY_ITEMS]


def resume_summary(state: EngineRunState) -> str:
    """What the journal says the run did, for an engine that lost its own history (ARCHITECTURE §5.4)."""
    lines = [f"Turns completed so far: {sum(1 for t in state.turns if t.completed)}."]
    if state.changed_paths:
        lines.append("Files saved in the workspace: " + ", ".join(p.lstrip("/") for p in state.changed_paths) + ".")
    if state.tools_called:
        lines.append("PipesHub tools already called: " + ", ".join(state.tools_called) + ".")
    unknown = [i.tool_id for i in state.invocations.values() if i.state == "unknown" and i.tool_id]
    if unknown:
        lines.append(
            "These calls may or may not have taken effect and will not run again: " + ", ".join(unknown) + "."
        )
    return "\n".join(lines)



def _said(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    try:
        return said_in(ContentRef.model_validate(value))
    except ValidationError:
        return None

__all__ = [
    "DEFERRED_SLOT",
    "ChildState",
    "EngineRunState",
    "TurnRecord",
    "checkpoint_data",
    "checkpoint_of",
    "fold_engine_run",
    "resume_summary",
    "snapshot_of",
]
