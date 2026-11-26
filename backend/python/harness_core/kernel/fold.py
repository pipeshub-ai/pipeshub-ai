"""Pure fold of the journal into ``SessionState`` and ``RunState`` (REPORT 6.1.6).

Both reducers are total: any event sequence yields a state or raises
``FoldIncompatible``. An unknown *critical* event (a type this build does not
know, a newer ``schemaVersion``, or a payload that fails its schema) means a
newer writer produced state this worker cannot interpret, so the caller
releases its claim. Unknown non-critical events are ignored.

The fold never dereferences content refs; rendering a transcript from the refs
is the caller's job.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, Field, JsonValue, ValidationError

from harness_core.context.results import RESULT_FOOTERS_SLOT
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.common import ContentRef, SeqRange
from harness_core.contracts.generated.event_catalog import EVENT_MODELS
from harness_core.contracts.generated.events.child_completed import ChildCompletedData
from harness_core.contracts.generated.events.child_spawned import ChildSpawnedData
from harness_core.contracts.generated.events.context_cleared import ContextClearedData
from harness_core.contracts.generated.events.context_compacted import (
    ContextCompactedData,
)
from harness_core.contracts.generated.events.input_consumed import InputConsumedData
from harness_core.contracts.generated.events.input_queued import (
    InputAttachment,
    InputQueuedData,
)
from harness_core.contracts.generated.events.interaction_requested import (
    InteractionRequestedData,
)
from harness_core.contracts.generated.events.interaction_resolved import (
    InteractionResolvedData,
)
from harness_core.contracts.generated.events.loop_state_saved import LoopStateSavedData
from harness_core.contracts.generated.events.message_completed import (
    MessageCompletedData,
)
from harness_core.contracts.generated.events.model_fallback import ModelFallbackData
from harness_core.contracts.generated.events.model_response import ModelResponseData
from harness_core.contracts.generated.events.run_admitted import RunAdmittedData
from harness_core.contracts.generated.events.run_claimed import RunClaimedData
from harness_core.contracts.generated.events.run_completed import RunCompletedData
from harness_core.contracts.generated.events.run_failed import RunFailedData
from harness_core.contracts.generated.events.run_spec_pinned import RunSpecPinnedData
from harness_core.contracts.generated.events.run_suspended import RunSuspendedData
from harness_core.contracts.generated.events.session_imported import SessionImportedData
from harness_core.contracts.generated.events.task_created import TaskCreatedData
from harness_core.contracts.generated.events.task_updated import TaskUpdatedData
from harness_core.contracts.generated.events.tool_denied import ToolDeniedData
from harness_core.contracts.generated.events.tool_intent import ToolIntentData
from harness_core.contracts.generated.events.tool_outcome import ToolOutcomeData
from harness_core.contracts.generated.events.tool_unknown import ToolUnknownData
from harness_core.contracts.generated.events.turn_completed import TurnCompletedData
from harness_core.contracts.generated.events.turn_started import TurnStartedData
from harness_core.contracts.generated.events.usage_recorded import UsageRecordedData
from harness_core.contracts.generated.events.workspace_committed import (
    WorkspaceCommittedData,
)
from harness_core.journal.types import TERMINAL_STATUSES, Checkpoint, RunStatus
from harness_core.kernel.session_tools import SESSION_TOOLS_SLOT
from harness_core.models.families import legacy_input_tokens

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.journal.types import JournalEvent

# 2: a no-tool turn followed by an injected continuation resumes at the next turn (S5b PA-3).
# 3: a journaled terminal answer (slot `terminal.answer`) ends the turn on resume instead of
#    re-sampling the model (S5c PA-14).
# 4: a tool result journaled as parts (text and images) is rendered as those parts, so a
#    resumed request carries the images the model saw (P1b C-1).
# 5: a consumed steering input (`input.consumed`) is a user message of the session where it
#    was consumed (P1b T2).
# 6: a steering input carries attachments and records by reference, and its consumption the
#    parts the model was given for it (`contentRef`), rendered as those parts (P1b C-21).
# 7: an imported legacy history (`session.imported`) is part of the session where Node journaled
#    it; one that replaces the history drops everything before it (P1b T3).
# 8: an invocation that spawned a child run (`child.spawned.invocationId`) waits on it; the batch
#    suspends on `join` until `child.completed` brings the result (P6.1).
# A worker older than a run's pinned foldVersion releases the claim instead of misreading it.
FOLD_VERSION = 8
SUPPORTED_SCHEMA_VERSION = 1

NextAction = Literal[
    "call_model", "dispatch_pending", "await_barrier", "suspended", "assemble", "done"
]
InvocationState = Literal[
    "pending",  # the model asked for it; nothing journaled yet
    "intent",  # fenced intent durable, no outcome
    "awaiting_approval",  # policy said ask; interaction open
    "approved",  # interaction approved: execute (or retry an unknown effect)
    "rejected",  # interaction denied/expired: record a denial outcome
    "unknown",  # effect may or may not have happened; a human decides
    "joining",  # a child run was admitted for it and has not ended
    "joined",  # its child ended; the result is not journaled as the outcome yet
    "completed",  # outcome durable
]
# connect (G-12): the call waits for the run's owner to connect the item it needs; it waits as an approval does.
InteractionPurpose = Literal["approve", "unknown", "connect"]
Integrity = Literal["trusted", "internal", "external"]
_INTEGRITY_ORDER: dict[str, int] = {"trusted": 0, "internal": 1, "external": 2}


class FoldIncompatible(Exception):
    """The journal holds a critical event this build cannot interpret: release the claim."""

    def __init__(self, event_type: str, seq: int, reason: str) -> None:
        super().__init__(f"seq {seq}: {event_type}: {reason}")
        self.event_type = event_type
        self.seq = seq
        self.reason = reason


def invocation_id(turn: int, index: int) -> str:
    """Deterministic from the journaled response, so every attempt derives the same id."""
    return f"t{turn}.{index}"


def parse_invocation_id(value: str) -> tuple[int, int] | None:
    head, _, tail = value.partition(".")
    if not head.startswith("t") or not head[1:].isdigit() or not tail.isdigit():
        return None
    return int(head[1:]), int(tail)


def interaction_id(invocation: str, purpose: InteractionPurpose) -> str:
    return f"ix.{purpose}.{invocation}"


def is_connect_interaction(interaction: str | None) -> bool:
    return (interaction or "").startswith("ix.connect.")


def parse_interaction_id(value: str) -> tuple[InteractionPurpose, str] | None:
    for purpose in ("approve", "unknown", "connect"):
        prefix = f"ix.{purpose}."
        if value.startswith(prefix):
            return purpose, value.removeprefix(prefix)
    return None


def join_integrity(a: str, b: str) -> Integrity:
    worst = a if _INTEGRITY_ORDER.get(a, 2) >= _INTEGRITY_ORDER.get(b, 2) else b
    return cast("Integrity", worst if worst in _INTEGRITY_ORDER else "external")


class ToolCallRef(BaseModel):
    call_id: str
    name: str
    args_digest: str


class UsageRecord(BaseModel):
    """The provider's count of one request; ``input_tokens`` is the whole prompt, cached tokens included."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ResponseRecord(BaseModel):
    seq: int
    turn: int
    message_ref: ContentRef
    raw_ref: ContentRef | None = None
    provider: str
    transport: str
    tool_calls: list[ToolCallRef] = Field(default_factory=list[ToolCallRef])
    # What the context compiler's ladder measures (P4.2).
    usage: UsageRecord | None = None


class Invocation(BaseModel):
    invocation_id: str
    turn: int
    index: int
    call_id: str
    name: str
    args_digest: str
    state: InvocationState = "pending"
    effect: str | None = None
    tool_id: str | None = None
    intent_attempt_id: str | None = None
    status: str | None = None
    result_ref: ContentRef | None = None
    receipt: dict[str, JsonValue] | None = None
    interaction_id: str | None = None
    decision: str | None = None
    denied_rule: str | None = None
    child_run_id: str | None = None
    # `tool.intent` events journaled for it, and the DB time of the last: a
    # reconcile's window starts there, and a second intent is a re-call (P2.9).
    intents: int = 0
    intent_ts: str | None = None


class InteractionRecord(BaseModel):
    kind: str
    invocation_id: str | None = None
    purpose: InteractionPurpose | None = None
    decision: str | None = None
    # What the person said with the decision: a question's answer, a denial's reason (AP-05).
    answer_ref: ContentRef | None = None


class Suspension(BaseModel):
    reason: str
    interaction_id: str | None = None


class Consumed(BaseModel):
    turns: int = 0
    model_requests: int = 0
    model_responses: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ChildRecord(BaseModel):
    child_run_id: str
    invocation_id: str | None = None
    completed: bool = False
    outcome: str | None = None
    error_type: str | None = None
    result_ref: ContentRef | None = None
    labels: dict[str, JsonValue] | None = None


class Cursor(BaseModel):
    next_action: NextAction
    turn: int
    pending: list[str] = Field(default_factory=list[str])


class ModelRouteRecord(BaseModel):
    """The pinned route a journaled ``model.fallback`` moved the run to (P4.7)."""

    provider: str
    model: str


class RunState(BaseModel):
    fold_version: int = FOLD_VERSION
    run_id: str
    seq: int = 0
    status: RunStatus = "queued"
    attempt: int = 0
    fence: int = 0
    spec_digest: str | None = None
    input_recorded: bool = False
    turn: int = 0
    turn_completed: int = 0
    responses: list[ResponseRecord] = Field(default_factory=list[ResponseRecord])
    invocations: dict[str, Invocation] = Field(default_factory=dict[str, Invocation])
    interactions: dict[str, InteractionRecord] = Field(
        default_factory=dict[str, InteractionRecord]
    )
    consumed: Consumed = Field(default_factory=Consumed)
    slots: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    children: dict[str, ChildRecord] = Field(default_factory=dict[str, ChildRecord])
    suspension: Suspension | None = None
    cancel_requested: bool = False
    # Steering inputs Node queued and no boundary consumed yet, in queue order.
    queued_inputs: list[int] = Field(default_factory=list[int])
    consumed_inputs: list[int] = Field(default_factory=list[int])
    final_message_ref: ContentRef | None = None
    outcome: str | None = None
    # Placeholder until labels flow (P2): the monotone join of outcome integrity labels.
    context_label: Integrity = "trusted"
    ignored_events: int = 0
    # The route the last ``model.fallback`` moved to; unset: the primary.
    model_route: ModelRouteRecord | None = None

    def response_for(self, turn: int) -> ResponseRecord | None:
        return next((r for r in self.responses if r.turn == turn), None)

    def invocations_for(self, turn: int) -> list[Invocation]:
        return sorted(
            (i for i in self.invocations.values() if i.turn == turn),
            key=lambda i: i.index,
        )

    @property
    def cursor(self) -> Cursor:
        if self.status in TERMINAL_STATUSES:
            return Cursor(next_action="done", turn=self.turn)
        if self.status == "suspended":
            return Cursor(next_action="suspended", turn=self.turn)
        response = self.response_for(self.turn) if self.turn else None
        if response is None:
            return Cursor(next_action="call_model", turn=max(self.turn, 1))
        if not response.tool_calls:
            # A no-tool turn completed without an answer was continued (a
            # response review asked for another turn).
            if self.turn_completed >= self.turn:
                return Cursor(next_action="call_model", turn=self.turn + 1)
            return Cursor(next_action="assemble", turn=self.turn)
        batch = self.invocations_for(self.turn)
        open_ids = [i.invocation_id for i in batch if i.state != "completed"]
        if not open_ids:
            return Cursor(next_action="call_model", turn=self.turn + 1)
        waiting = [
            i
            for i in batch
            if i.state in ("awaiting_approval", "joining")
            or (i.state == "unknown" and i.interaction_id is not None)
        ]
        if waiting:
            settled = all(
                i.state in ("completed", "awaiting_approval", "unknown", "joining")
                for i in batch
            )
            action: NextAction = "suspended" if settled else "await_barrier"
            return Cursor(next_action=action, turn=self.turn, pending=open_ids)
        return Cursor(next_action="dispatch_pending", turn=self.turn, pending=open_ids)


class ResultRecord(BaseModel):
    invocation_id: str
    status: str
    result_ref: ContentRef | None = None
    # The outcome's seq and item labels: what a reduction's range and label cover (P4.2).
    seq: int | None = None
    labels: dict[str, JsonValue] | None = None


class CompactionRecord(BaseModel):
    """A journaled ``context.compacted``: the range, its summary, the join of the range's labels."""

    seq: int
    range: SeqRange
    summary_ref: ContentRef
    labels: dict[str, JsonValue] | None = None


class QueuedInput(BaseModel):
    """A steering message Node queued: its text and what it names by reference."""

    text_ref: ContentRef | None = None
    attachments: list[InputAttachment] = Field(default_factory=list[InputAttachment])
    record_ids: list[str] = Field(default_factory=list[str])

    @property
    def has_references(self) -> bool:
        return bool(self.attachments or self.record_ids)


class SessionEntry(BaseModel):
    seq: int
    run_id: str | None
    # "imported": a legacy history, ``text_ref`` its transcript (``session.imported``).
    role: Literal["user", "assistant", "imported"]
    text_ref: ContentRef | None = None
    injected: bool = False
    # A steering input: its `messageSeq`, placed where a boundary consumed it.
    input_seq: int | None = None
    # The steering input's parts (text, attachments' text and images) when it carried references.
    content_ref: ContentRef | None = None
    response: ResponseRecord | None = None
    results: dict[str, ResultRecord] = Field(default_factory=dict[str, ResultRecord])


class TaskRecord(BaseModel):
    title: str
    status: str
    deps: list[str] = Field(default_factory=list[str])


class SessionState(BaseModel):
    fold_version: int = FOLD_VERSION
    stream_id: str
    seq: int = 0
    entries: list[SessionEntry] = Field(default_factory=list[SessionEntry])
    compacted: list[SeqRange] = Field(default_factory=list[SeqRange])
    cleared: list[SeqRange] = Field(default_factory=list[SeqRange])
    # The context compiler's view of ``compacted`` (P4.2), and the seq of the latest reduction.
    compactions: list[CompactionRecord] = Field(default_factory=list[CompactionRecord])
    reduced_at_seq: int = 0
    # The step footers compiled requests showed on results, by call id, every run's (P4.9).
    result_footers: dict[str, str] = Field(default_factory=dict[str, str])
    # The session's tool list by list key, as its first request journaled it (R4): ``{"ref", "digest", "count"}``.
    tool_lists: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])
    workspace_heads: dict[str, int] = Field(default_factory=dict[str, int])
    open_interactions: dict[str, str] = Field(default_factory=dict[str, str])
    tasks: dict[str, TaskRecord] = Field(default_factory=dict[str, TaskRecord])
    # Placeholders until memory (P7) and session docs (6.10) are journaled.
    memory_refs: list[str] = Field(default_factory=list[str])
    doc_labels: dict[str, str] = Field(default_factory=dict[str, str])
    run_turns: dict[str, int] = Field(default_factory=dict[str, int])
    # Queued steering inputs by `runId:messageSeq`, until a boundary consumes them.
    queued_inputs: dict[str, QueuedInput] = Field(default_factory=dict[str, QueuedInput])
    ignored_events: int = 0
    # Runs admitted as a person's command (``runKind: command``, SLASH3 ``/run``): never in another run's context.
    command_runs: list[str] = Field(default_factory=list[str])
    # Every folded usage counts cached tokens inside ``input_tokens``; a checkpoint without this
    # field folded them as each reporter counted, and is refolded.
    usage_includes_cached: bool = True


# ---- event payload parsing ----


@cache
def _data_model(event_type: str) -> type[BaseModel] | None:
    model = EVENT_MODELS.get(event_type)
    if model is None:
        return None
    return cast("type[BaseModel]", model.model_fields["data"].annotation)


def _parse(event: JournalEvent) -> BaseModel | None:
    """Typed payload, ``None`` for an ignorable event, or ``FoldIncompatible``."""
    data_model = _data_model(event.type)
    if data_model is None:
        if event.critical:
            raise FoldIncompatible(event.type, event.seq, "unknown critical event type")
        return None
    if event.schema_version > SUPPORTED_SCHEMA_VERSION:
        if event.critical:
            raise FoldIncompatible(
                event.type, event.seq, f"schemaVersion {event.schema_version}"
            )
        return None
    try:
        return validate_contract(data_model, event.data)
    except ValidationError as exc:
        if event.critical:
            raise FoldIncompatible(
                event.type, event.seq, "payload fails its schema"
            ) from exc
        return None


# ---- run fold ----


def fold_run(
    events: Iterable[JournalEvent], *, run_id: str, state: RunState | None = None
) -> RunState:
    """Fold ``events`` (one stream, ascending seq) into the state of ``run_id``."""
    out = state.model_copy(deep=True) if state is not None else RunState(run_id=run_id)
    if out.run_id != run_id:
        raise ValueError(f"state is for run {out.run_id!r}, not {run_id!r}")
    for event in events:
        if event.seq <= out.seq:
            continue
        payload = _parse(event)
        out.seq = event.seq
        if payload is None:
            out.ignored_events += 1
            continue
        if event.run_id == run_id:
            _apply_run(out, event, payload)
    return out


def _apply_run(state: RunState, event: JournalEvent, payload: BaseModel) -> None:
    match payload:
        case RunClaimedData(attempt=attempt, fence=fence):
            state.attempt, state.fence, state.status = attempt, fence, "running"
        case RunSpecPinnedData(spec_digest=digest):
            state.spec_digest = digest
        case RunSuspendedData(reason=reason, interaction_id=ix):
            state.status = "suspended"
            state.suspension = Suspension(reason=reason, interaction_id=ix)
        case RunCompletedData(outcome=outcome):
            state.status, state.outcome = "completed", outcome
        case RunFailedData(error_type=error_type):
            state.status, state.outcome = "failed", error_type
        case TurnStartedData(turn=turn):
            state.turn = max(state.turn, turn)
            state.consumed.turns = max(state.consumed.turns, turn)
        case TurnCompletedData(turn=turn):
            state.turn_completed = max(state.turn_completed, turn)
        case ModelResponseData():
            _apply_response(state, event, payload)
        case ModelFallbackData(to=to):
            state.model_route = ModelRouteRecord(provider=to.provider, model=to.model)
        case MessageCompletedData(role=role, text_ref=ref, is_final=is_final):
            if role == "user":
                state.input_recorded = True
            elif is_final:
                state.final_message_ref = ref
        case ToolIntentData():
            _apply_intent(state, event, payload)
        case ToolDeniedData(invocation_id=inv, rule=rule, tool_id=tool_id):
            if (target := state.invocations.get(inv)) is not None:
                target.denied_rule = rule
                if tool_id is not None and target.tool_id is None:
                    target.tool_id = tool_id
        case ToolOutcomeData():
            _apply_outcome(state, payload)
        case ToolUnknownData(invocation_id=inv):
            if (
                target := state.invocations.get(inv)
            ) is not None and target.state != "completed":
                target.state = "unknown"
        case InteractionRequestedData(interaction_id=ix, kind=kind):
            _apply_interaction_requested(state, ix, kind)
        case InteractionResolvedData(interaction_id=ix, decision=decision, answer_ref=answer):
            _apply_interaction_resolved(state, ix, decision, answer)
        case LoopStateSavedData(slots=slots):
            state.slots.update(cast("dict[str, JsonValue]", slots))
        case ChildSpawnedData():
            _apply_child_spawned(state, payload)
        case ChildCompletedData():
            _apply_child_completed(state, payload)
        case InputQueuedData(message_seq=message_seq):
            if message_seq in state.queued_inputs or message_seq in state.consumed_inputs:
                state.ignored_events += 1
            else:
                state.queued_inputs.append(message_seq)
        case InputConsumedData(message_seq=message_seq):
            if message_seq in state.queued_inputs:
                state.queued_inputs.remove(message_seq)
                state.consumed_inputs.append(message_seq)
            else:
                state.ignored_events += 1
        case UsageRecordedData(tokens=tokens):
            _add_usage(
                state.consumed,
                tokens.input_tokens,
                tokens.output_tokens,
                tokens.cache_read_tokens,
                tokens.cache_write_tokens,
            )
        case _:
            _apply_lifecycle(state, event.type)


def _apply_lifecycle(state: RunState, event_type: str) -> None:
    if event_type in ("run.resumed", "run.yielded"):
        state.status, state.suspension = "queued", None
    elif event_type == "run.cancel_requested":
        state.cancel_requested = True
    elif event_type == "run.cancelled":
        state.status = "cancelled"
    elif event_type == "run.quarantined":
        state.status = "quarantined"
    elif event_type == "model.request":
        state.consumed.model_requests += 1


def _add_usage(
    consumed: Consumed, i: int, o: int, cr: int | None, cw: int | None
) -> None:
    consumed.input_tokens += i
    consumed.output_tokens += o
    consumed.cache_read_tokens += cr or 0
    consumed.cache_write_tokens += cw or 0


def _apply_response(
    state: RunState, event: JournalEvent, data: ModelResponseData
) -> None:
    turn = max(state.turn, 1)
    if state.response_for(turn) is not None:
        # Never re-sampled by a live worker; a duplicate can only be a replayed write.
        state.ignored_events += 1
        return
    calls = [
        ToolCallRef(call_id=c.call_id, name=c.model_name, args_digest=c.args_digest)
        for c in data.tool_calls
    ]
    state.responses.append(
        ResponseRecord(
            seq=event.seq,
            turn=turn,
            message_ref=data.message_ref,
            raw_ref=data.raw_ref,
            provider=data.provider,
            transport=data.transport,
            tool_calls=calls,
            usage=_usage_record(data),
        )
    )
    state.consumed.model_responses += 1
    usage = _usage_record(data)
    _add_usage(
        state.consumed,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_tokens,
        usage.cache_write_tokens,
    )
    for index, call in enumerate(calls):
        inv = invocation_id(turn, index)
        state.invocations[inv] = Invocation(
            invocation_id=inv,
            turn=turn,
            index=index,
            call_id=call.call_id,
            name=call.name,
            args_digest=call.args_digest,
        )


def journaled_input_tokens(
    input_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    *,
    includes_cached: bool | None,
    provider: str,
    transport: str | None,
) -> int:
    """A ``model.response``'s whole prompt as the fold counts it (the grant auditor's reconciliation reads it too)."""
    if includes_cached:
        return input_tokens
    # Journaled with its reporter's own count: only provider and transport say what that was.
    return legacy_input_tokens(
        input_tokens, cache_read_tokens, cache_write_tokens, provider=provider, transport=transport
    )


def _usage_record(data: ModelResponseData) -> UsageRecord:
    usage = data.usage
    read, written = usage.cache_read_tokens or 0, usage.cache_write_tokens or 0
    input_tokens = journaled_input_tokens(
        usage.input_tokens,
        read,
        written,
        includes_cached=usage.input_includes_cached,
        provider=data.provider,
        transport=data.transport,
    )
    return UsageRecord(
        input_tokens=input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=read,
        cache_write_tokens=written,
    )


def _apply_intent(state: RunState, event: JournalEvent, data: ToolIntentData) -> None:
    target = state.invocations.get(data.invocation_id)
    if target is None or target.state == "completed":
        state.ignored_events += 1
        return
    target.effect = data.effect
    target.tool_id = data.tool_id
    target.intent_attempt_id = event.attempt_id
    target.intents += 1
    target.intent_ts = event.ts
    if target.state in ("pending", "approved"):
        target.state = "intent"


def _apply_outcome(state: RunState, data: ToolOutcomeData) -> None:
    target = state.invocations.get(data.invocation_id)
    if target is None or target.state == "completed":
        state.ignored_events += 1
        return
    target.state = "completed"
    target.status = data.status
    target.result_ref = data.result_ref
    target.receipt = cast("dict[str, JsonValue] | None", data.receipt)
    state.context_label = join_integrity(state.context_label, data.labels.integrity)


def _apply_child_spawned(state: RunState, data: ChildSpawnedData) -> None:
    child = data.child_run_id
    if child in state.children:
        state.ignored_events += 1
        return
    inv = data.invocation_id
    state.children[child] = ChildRecord(child_run_id=child, invocation_id=inv)
    target = state.invocations.get(inv) if inv is not None else None
    if target is not None and target.state not in ("completed", "joined"):
        target.state = "joining"
        target.child_run_id = child


def _apply_child_completed(state: RunState, data: ChildCompletedData) -> None:
    record = state.children.get(data.child_run_id) or ChildRecord(
        child_run_id=data.child_run_id
    )
    if record.completed:
        state.ignored_events += 1
        return
    state.children[data.child_run_id] = record.model_copy(
        update={
            "completed": True,
            "outcome": data.outcome,
            "error_type": data.error_type,
            "result_ref": data.result_ref,
            "labels": cast(
                "dict[str, JsonValue]",
                data.labels.model_dump(mode="json", by_alias=True, exclude_none=True),
            ),
        }
    )
    inv = record.invocation_id
    target = state.invocations.get(inv) if inv is not None else None
    if target is not None and target.state == "joining":
        target.state = "joined"


def _apply_interaction_requested(state: RunState, ix: str, kind: str) -> None:
    parsed = parse_interaction_id(ix)
    record = InteractionRecord(kind=kind)
    if parsed is not None:
        record.purpose, record.invocation_id = parsed
        target = state.invocations.get(parsed[1])
        if target is not None and target.state != "completed":
            target.interaction_id = ix
            if parsed[0] in ("approve", "connect"):
                target.state = "awaiting_approval"
    state.interactions[ix] = record


def _apply_interaction_resolved(state: RunState, ix: str, decision: str, answer: ContentRef | None = None) -> None:
    record = state.interactions.get(ix)
    if record is None:
        state.ignored_events += 1
        return
    record.decision = decision
    record.answer_ref = answer
    if record.invocation_id is None:
        return
    target = state.invocations.get(record.invocation_id)
    if target is None or target.state == "completed":
        return
    target.decision = decision
    target.state = "approved" if decision == "approved" else "rejected"


# ---- session fold ----


def fold_session(
    events: Iterable[JournalEvent], *, stream_id: str, state: SessionState | None = None
) -> SessionState:
    """Fold a session's root stream (every run in it) into ``SessionState``."""
    out = (
        state.model_copy(deep=True)
        if state is not None
        else SessionState(stream_id=stream_id)
    )
    for event in events:
        if event.seq <= out.seq:
            continue
        payload = _parse(event)
        out.seq = event.seq
        if payload is None:
            out.ignored_events += 1
            continue
        _apply_session(out, event, payload)
    return out


def _apply_session(
    state: SessionState, event: JournalEvent, payload: BaseModel
) -> None:
    run_key = event.run_id or ""
    match payload:
        case TurnStartedData(turn=turn):
            state.run_turns[run_key] = max(state.run_turns.get(run_key, 0), turn)
        case RunAdmittedData(run_kind="command") if run_key and run_key not in state.command_runs:
            state.command_runs.append(run_key)
        case MessageCompletedData(role="user", text_ref=ref, injected=injected):
            state.entries.append(
                SessionEntry(
                    seq=event.seq,
                    run_id=event.run_id,
                    role="user",
                    text_ref=ref,
                    injected=bool(injected),
                )
            )
        case ModelResponseData():
            _session_response(state, event, payload)
        case InputQueuedData(
            message_seq=message_seq, text_ref=ref, attachments=attachments, record_ids=records
        ):
            _ = state.queued_inputs.setdefault(
                f"{run_key}:{message_seq}",
                QueuedInput(
                    text_ref=ref,
                    attachments=list(attachments or ()),
                    record_ids=[r.root for r in records or ()],
                ),
            )
        case InputConsumedData(message_seq=message_seq, content_ref=content):
            _session_consumed(state, event, message_seq, content)
        case SessionImportedData(transcript_ref=ref, replaces_history=replaces):
            _session_imported(state, event, ref, replaces=bool(replaces))
        case ToolOutcomeData():
            _session_outcome(state, run_key, event.seq, payload)
        case ContextCompactedData(range=seq_range, summary_ref=summary):
            state.compacted.append(seq_range)
            state.compactions.append(
                CompactionRecord(
                    seq=event.seq,
                    range=seq_range,
                    summary_ref=summary,
                    labels=event.labels,
                )
            )
            state.reduced_at_seq = event.seq
        case ContextClearedData(range=seq_range):
            state.cleared.append(seq_range)
            state.reduced_at_seq = event.seq
        case LoopStateSavedData(slots=slots):
            footers = cast("dict[str, JsonValue]", slots).get(RESULT_FOOTERS_SLOT)
            if isinstance(footers, dict):
                state.result_footers.update({str(k): str(v) for k, v in footers.items()})
            lists = cast("dict[str, JsonValue]", slots).get(SESSION_TOOLS_SLOT)
            if isinstance(lists, dict):
                # The first list journaled under a key is the session's; a later attempt's duplicate never moves it.
                for key, entry in lists.items():
                    _ = state.tool_lists.setdefault(str(key), entry)
        case WorkspaceCommittedData(workspace_id=workspace, revision=revision):
            state.workspace_heads[workspace] = max(
                state.workspace_heads.get(workspace, 0), revision
            )
        case InteractionRequestedData(interaction_id=ix, kind=kind):
            state.open_interactions[ix] = kind
        case InteractionResolvedData(interaction_id=ix):
            _ = state.open_interactions.pop(ix, None)
        case (
            TaskCreatedData(task_id=task, title=title, status=status, deps=deps)
            | TaskUpdatedData(task_id=task, title=title, status=status, deps=deps)
        ):
            state.tasks[task] = TaskRecord(
                title=title, status=status, deps=[d.root for d in deps]
            )
        case _:
            pass


def _session_response(
    state: SessionState, event: JournalEvent, data: ModelResponseData
) -> None:
    run_key = event.run_id or ""
    turn = max(state.run_turns.get(run_key, 0), 1)
    duplicate = any(
        e.response is not None and e.run_id == event.run_id and e.response.turn == turn
        for e in state.entries
    )
    if duplicate:
        state.ignored_events += 1
        return
    record = ResponseRecord(
        seq=event.seq,
        turn=turn,
        message_ref=data.message_ref,
        raw_ref=data.raw_ref,
        provider=data.provider,
        transport=data.transport,
        tool_calls=[
            ToolCallRef(call_id=c.call_id, name=c.model_name, args_digest=c.args_digest)
            for c in data.tool_calls
        ],
        usage=_usage_record(data),
    )
    state.entries.append(
        SessionEntry(
            seq=event.seq, run_id=event.run_id, role="assistant", response=record
        )
    )


def _session_consumed(
    state: SessionState, event: JournalEvent, message_seq: int, content: ContentRef | None
) -> None:
    key = f"{event.run_id or ''}:{message_seq}"
    if key not in state.queued_inputs:
        state.ignored_events += 1
        return
    state.entries.append(
        SessionEntry(
            seq=event.seq,
            run_id=event.run_id,
            role="user",
            text_ref=state.queued_inputs.pop(key).text_ref,
            input_seq=message_seq,
            content_ref=content,
        )
    )


def _session_imported(
    state: SessionState, event: JournalEvent, ref: ContentRef | None, *, replaces: bool
) -> None:
    # Node imports only while no root run of the session is active, so what a
    # replacing import drops belongs to runs that ended.
    if replaces:
        state.entries = []
    if ref is not None:
        state.entries.append(
            SessionEntry(seq=event.seq, run_id=event.run_id, role="imported", text_ref=ref)
        )


def _session_outcome(
    state: SessionState, run_key: str, seq: int, data: ToolOutcomeData
) -> None:
    inv = data.invocation_id
    parsed = parse_invocation_id(inv)
    if parsed is None:
        return
    turn, index = parsed
    for entry in reversed(state.entries):
        response = entry.response
        if response is None or (entry.run_id or "") != run_key or response.turn != turn:
            continue
        if index < len(response.tool_calls):
            call_id = response.tool_calls[index].call_id
            _ = entry.results.setdefault(
                call_id,
                ResultRecord(
                    invocation_id=inv,
                    status=data.status,
                    result_ref=data.result_ref,
                    seq=seq,
                    labels=cast(
                        "dict[str, JsonValue]",
                        data.labels.model_dump(mode="json", by_alias=True, exclude_none=True),
                    ),
                ),
            )
        return


# ---- checkpoints ----


def make_checkpoint(run: RunState, session: SessionState) -> Checkpoint:
    if run.seq != session.seq:
        raise ValueError("run and session folds must end at the same seq")
    return Checkpoint(
        run_id=run.run_id,
        stream_id=session.stream_id,
        fold_version=FOLD_VERSION,
        seq=run.seq,
        state={
            "run": run.model_dump(mode="json"),
            "session": session.model_dump(mode="json"),
        },
    )


# Derived state an older build's checkpoint lacks (P4.2): such a checkpoint is refolded.
_SESSION_STATE_FIELDS = ("compactions", "reduced_at_seq", "result_footers", "usage_includes_cached", "tool_lists")
_INVOCATION_FIELDS = ("intents", "intent_ts")
# P4.7.
_RUN_STATE_FIELDS = ("model_route",)


def checkpoint_is_valid(
    checkpoint: Checkpoint | None,
    *,
    run_id: str,
    head_seq: int,
    ref: str | None,
    referenced_ref: str | None,
) -> bool:
    """Valid only if the fold version matches, ``seq <= head``, the run document references
    it and it holds every piece of derived state this build folds."""
    return (
        checkpoint is not None
        and ref is not None
        and ref == referenced_ref
        and checkpoint.run_id == run_id
        and checkpoint.fold_version == FOLD_VERSION
        and checkpoint.seq <= head_seq
        and _current_shape(checkpoint)
    )


def _current_shape(checkpoint: Checkpoint) -> bool:
    session = checkpoint.state.get("session")
    if not (isinstance(session, dict) and all(name in session for name in _SESSION_STATE_FIELDS)):
        return False
    run = checkpoint.state.get("run")
    if not (isinstance(run, dict) and all(name in run for name in _RUN_STATE_FIELDS)):
        return False
    invocations = run.get("invocations")
    if not isinstance(invocations, dict):
        return False
    return all(
        isinstance(inv, dict) and all(name in inv for name in _INVOCATION_FIELDS)
        for inv in invocations.values()
    )


def restore_checkpoint(checkpoint: Checkpoint) -> tuple[RunState, SessionState]:
    run = RunState.model_validate(checkpoint.state["run"])
    session = SessionState.model_validate(checkpoint.state["session"])
    return run, session


__all__ = [
    "FOLD_VERSION",
    "SUPPORTED_SCHEMA_VERSION",
    "ChildRecord",
    "CompactionRecord",
    "Consumed",
    "Cursor",
    "FoldIncompatible",
    "InteractionPurpose",
    "is_connect_interaction",
    "InteractionRecord",
    "Invocation",
    "InvocationState",
    "NextAction",
    "QueuedInput",
    "ResponseRecord",
    "ResultRecord",
    "RunState",
    "SessionEntry",
    "SessionState",
    "Suspension",
    "TaskRecord",
    "ToolCallRef",
    "UsageRecord",
    "checkpoint_is_valid",
    "fold_run",
    "fold_session",
    "interaction_id",
    "invocation_id",
    "join_integrity",
    "make_checkpoint",
    "parse_interaction_id",
    "parse_invocation_id",
    "restore_checkpoint",
]
