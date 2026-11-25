"""Value types shared by the journal ports and their adapters (REPORT 6.1.2-6.1.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from harness_core.clock import StoreTime
from harness_core.contracts.generated.common import ContentRef
from harness_core.contracts.generated.enums import (
    CompletedOutcome,
    CutShortReason,
    InteractionChannel,
    InteractionDecision,
    PriorityClass,
    RunStatus,
    SuspendReason,
    Visibility,
    Writer,
    YieldReason,
)
from harness_core.contracts.generated.run_spec import RunSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.clock import Clock

TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {"completed", "failed", "cancelled", "quarantined"}
)


class JournalEvent(BaseModel):
    """One stored journal event with its payload inline (claim-checked payloads resolved)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    org_id: str
    session_id: str
    stream_id: str
    seq: int
    run_id: str | None = None
    attempt_id: str | None = None
    fence: int | None = None
    type: str
    schema_version: int = 1
    critical: bool
    ts: str
    writer: Writer
    visibility: Visibility = "owner"
    labels: dict[str, JsonValue] | None = None
    data: dict[str, JsonValue]

    @classmethod
    def from_envelope(cls, document: Mapping[str, Any]) -> JournalEvent:
        """The inverse of ``envelope``, with the wire defaults for omitted members."""
        return cls(
            org_id=document["orgId"],
            session_id=document["sessionId"],
            stream_id=document["streamId"],
            seq=document["seq"],
            run_id=document.get("runId"),
            attempt_id=document.get("attemptId"),
            fence=document.get("fence"),
            type=document["type"],
            schema_version=document.get("schemaVersion", 1),
            critical=document["critical"],
            ts=document["ts"],
            writer=document["writer"],
            visibility=document.get("visibility", "owner"),
            labels=document.get("labels"),
            data=document.get("data") or {},
        )

    def envelope(self) -> dict[str, JsonValue]:
        """Wire form (camelCase envelope, REPORT 6.1.2)."""
        doc: dict[str, JsonValue] = {
            "orgId": self.org_id,
            "sessionId": self.session_id,
            "streamId": self.stream_id,
            "seq": self.seq,
            "type": self.type,
            "schemaVersion": self.schema_version,
            "critical": self.critical,
            "ts": self.ts,
            "writer": self.writer,
            "visibility": self.visibility,
            "data": self.data,
        }
        if self.run_id is not None:
            doc["runId"] = self.run_id
        if self.attempt_id is not None:
            doc["attemptId"] = self.attempt_id
        if self.fence is not None:
            doc["fence"] = self.fence
        if self.labels is not None:
            doc["labels"] = self.labels
        return doc


@dataclass(frozen=True)
class NewEvent:
    """An event a worker asks the journal to append; seq, ts and ids come from the journal."""

    type: str
    data: dict[str, JsonValue]
    critical: bool = True
    visibility: Visibility = "owner"
    labels: dict[str, JsonValue] | None = None


@dataclass(frozen=True)
class AttemptToken:
    """What the claim hands the worker; every commit and renew presents it (REPORT 6.1.8)."""

    run_id: str
    attempt_id: str
    fence: int


@dataclass(frozen=True)
class Suspend:
    reason: SuspendReason
    interaction_id: str | None = None
    kind: Literal["suspend"] = "suspend"


@dataclass(frozen=True)
class Yield:
    not_before: str
    reason: YieldReason
    checkpoint_ref: str | None = None
    kind: Literal["yield"] = "yield"


@dataclass(frozen=True)
class ChildResultRef:
    """A child run's result (P6.1): its sub-agent result document and its label."""

    result_ref: ContentRef
    labels: dict[str, JsonValue]


@dataclass(frozen=True)
class Complete:
    outcome: CompletedOutcome = "answered"
    # The run ends although the user queued input it will not take (no turn left):
    # without it the journal refuses to complete a run with queued input.
    leaves_input: bool = False
    # A child run completes with its result, which Node hands its parent.
    child_result: ChildResultRef | None = None
    # A limit cut the run's work short and it answered from what it had gathered (BUDGETEND).
    truncated_by: CutShortReason | None = None
    kind: Literal["complete"] = "complete"


@dataclass(frozen=True)
class Fail:
    error_type: str
    message: str | None = None
    # ``mcp_tool_withdrawn``: the labels of the servers that withdrew a tool, so a client words the failure in the
    # reader's language and still names them (run.failed.servers).
    servers: tuple[str, ...] = ()
    # ``out_of_memory``: the sandbox's memory limit in MB, so the reader is told which limit the work met
    # (run.failed.memoryLimitMb).
    memory_limit_mb: int | None = None
    kind: Literal["fail"] = "fail"


@dataclass(frozen=True)
class Cancelled:
    kind: Literal["cancelled"] = "cancelled"


@dataclass(frozen=True)
class Quarantine:
    attempts: int
    last_error_type: str | None = None
    kind: Literal["quarantine"] = "quarantine"


# ``run.*`` events are Node-written (EVENT_WRITERS): a worker asks for the
# transition and the journal writes the event in the same transaction.
Transition = Annotated[
    Suspend | Yield | Complete | Fail | Cancelled | Quarantine,
    Field(discriminator="kind"),
]


def command_id(attempt_id: str, kind: str, n: int) -> str:
    """A worker's ``n``-th commit of an attempt: a retried commit keeps its id, so the
    journal answers it from its command table instead of appending twice."""
    return f"{attempt_id}:{kind}:{n}"


@dataclass(frozen=True)
class CommitRequest:
    command_id: str
    token: AttemptToken
    events: tuple[NewEvent, ...] = ()
    transition: Transition | None = None


@dataclass(frozen=True)
class CommitReceipt:
    command_id: str
    stream_id: str
    events: tuple[JournalEvent, ...]
    replayed: bool = False

    @property
    def last_seq(self) -> int | None:
        return self.events[-1].seq if self.events else None


@dataclass(frozen=True)
class InputArtifactRef:
    """An artifact the run's input names for the run to work on; ``version`` ``None``: its latest."""

    artifact_id: str
    version: int | None = None


# What a worker writes only when the journal of its claim accepts it (UX2, ``NodeCapabilities``), the mirror of
# the worker's own features. Without the name the worker does what it did before the shape existed.
APPROVAL_REASONS: Final = "approval_reasons"  # interaction.explained for the flow rule and an engine's default, hence conversation grants
APPROVAL_RULE_LAYERS: Final = "approval_rule_layers"  # interaction.explained naming the rule's layer, and a layer's own rules (QA6-F6)
CHILD_LABELS: Final = "child_labels"  # a child request carrying its parent's label
CHILD_GRANTS: Final = "child_grants"  # a child request carrying the conversation grants its parent holds (FLOWASK)
CHILD_TASKS: Final = "child_tasks"  # a child request carrying its task's parts, kept for the job's brief (BGUI-10)
CHILD_MCP_PINS: Final = "child_mcp_pins"  # a child request carrying its parent's first-use MCP pins (R31HOT2, D-LAZY-5)
COMMAND_ACTIVITY: Final = "command_activity"  # x.pipeshub.command.finished (UX5)
TASK_ASKS_FIRST: Final = "task_asks_first"  # task.created/task.updated carrying asksFirst (UXR-B1)
FOLLOWUPS: Final = "followups"  # the run's suggested next questions, kept by Node after the answer (UX5)
INTENT_ALLOWED_BY: Final = "intent_allowed_by"  # tool.intent naming the rule that let the call run without a person (CODEAUTO)
CONTEXT_COMPACTED: Final = "context_compacted"  # x.pipeshub.context.compacted, the person's /compact (SLASH2 C2)
CONTEXT_COMPACTED_AUTO: Final = "context_compacted_auto"  # that event with trigger auto: a compaction nobody asked for (LC2FIX-3)
APPROVAL_RULE_GRANTS: Final = "approval_rule_grants"  # interaction.explained carrying ruleGrant: main's "Don't ask again" (BUGS-B)
SKILL_LABELS: Final = "skill_labels"  # x.pipeshub.citations.recorded carrying skillLabels: what an agent-written skill brings (FOLLOWUPS1-1)
# interaction.requested with kind connect: the run waits for its owner to connect an item a call needs (G-12)
CONNECT_PAUSES: Final = "connect_pauses"
NATIVE_EVENTS: Final = "native_events"  # x.pipeshub.native.unknown and .transcript: what an engine sent natively (C2.39, C2.40)
APPROVAL_EDITS: Final = "approval_edits"  # interaction.explained offering editable; an approval carrying the edit (AP-06)
# interaction.explained carrying fieldOrder: the tool's argument order for the edit card (WP-56)
APPROVAL_EDIT_ORDER: Final = "approval_edit_order"
# interaction.explained carrying argsDigest: what a person's decline covers when the ask names no destination (R38-AUTHZ)
APPROVAL_ARGS_DIGEST: Final = "approval_args_digest"
# interaction.explained with rule external_policy: the org's policy webhook asks for a person (G-40, WP-39)
EXTERNAL_POLICY_ASKS: Final = "external_policy_asks"
# x.pipeshub.knowledge.exported: a run's knowledge export to its sandbox; the export tool is offered only then (G-35)
KNOWLEDGE_EXPORTS: Final = "knowledge_exports"
# A claim-next naming aboveClass, and run.yielded with reason preempted (G-45)
PRIORITY_PREEMPTION: Final = "priority_preemption"
# x.pipeshub.repository.fetched: a public repository the worker fetched for an engine run (WP-63 part A)
REPOSITORY_FETCHES: Final = "repository_fetches"
# interaction.explained with rule site_upload: the upload card for a read-write website held after a private read
SITE_UPLOAD_ASKS: Final = "site_upload_asks"
# x.pipeshub.outcome.recorded and .settled: the run's outcome ledger and its one settled outcome (ADR-RUN-01 D1)
OUTCOME_LEDGER: Final = "outcome_ledger"
# x.pipeshub.outcome.settled carrying presented: the chat words the outcome, the answer carries only the prose (WP-89)
OUTCOME_WORDS: Final = "outcome_words"
# x.pipeshub.provenance.recorded: each tool result's provenance, the session's ProvenanceLedger (ADR-RUN-01 D3)
PROVENANCE_RECORDS: Final = "provenance_records"
# x.pipeshub.answer.finalized limits naming a file of the turn the model never read (R33JGAPS-FU-6)
ATTACHMENT_LIMITS: Final = "attachment_limits"
# x.pipeshub.answer.finalized and tool-result limits naming a repository the task named and the run never cloned (R41-CLONE)
REPOSITORY_NOT_CLONED: Final = "repository_not_cloned"
# turn.started carrying skippedInputs: an input artifact the job refused on its own is skipped and named (R36COVER-FU-3)
SKIPPED_INPUTS: Final = "skipped_inputs"
# could-not-do limits naming a file handed to a job over the per-input cap as attachment.too_large (R46FIX-2)
ATTACHMENT_TOO_LARGE: Final = "attachment_too_large"
# could-not-do limits counting the files handed to a job it may not name, one item with count (R46FIX-FU-2)
ATTACHMENT_COUNT: Final = "attachment_count"


@dataclass(frozen=True)
class CompactAsk:
    """The person's ``/compact [focus]`` the run's input carries (SLASH2 C2): honoured once, before the run's first
    request; ``focus`` is what they asked the summary to keep."""

    focus: str | None = None


@dataclass(frozen=True)
class Claim:
    """A claimed attempt: the pinned spec, the fence and the stream head at claim time."""

    run_id: str
    org_id: str
    session_id: str
    stream_id: str
    attempt_id: str
    attempt: int
    fence: int
    lease_ms: int
    last_seq: int
    spec: RunSpec
    input_text: str
    checkpoint_ref: str | None = None
    # The deployment capabilities pinned with the spec (REPORT 11); the
    # assembler builds nothing the pinned set does not allow.
    capabilities: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Artifacts the user named with the run's input (E1b); an engine run finds them under ``in/``.
    input_artifacts: tuple[InputArtifactRef, ...] = ()
    # What the journal that handed out this claim accepts beyond every v2 journal (UX2, ``NodeCapabilities``);
    # ``None``: a journal of this build, which accepts everything it writes.
    accepts: frozenset[str] | None = None
    compact: CompactAsk | None = None
    # What fired a scheduled run (TR-03): ids and times only, never outsider-written content. An engine run finds
    # it at ``in/trigger.json``; the loop is told it in words.
    trigger_context: Mapping[str, JsonValue] | None = None
    # Which runs go first for a slot (G-45), as the claim named it; ``None``: a journal that names none (an older
    # Node), whose runs are all served alike.
    priority_class: PriorityClass | None = None
    # Items (instance ids) the person chose not to connect, or did not in time, anywhere in this run's tree: its
    # root, its parent, a sibling, any sub-agent (WP-64). Only the journal sees every stream of the tree, so it names
    # them at the claim; the run is never asked for one again.
    skipped_connects: frozenset[str] = frozenset()
    # When the run's deadline ends it, epoch ms (a child's is its parent's); ``None``: a journal that names none.
    deadline_at_ms: int | None = None
    # How far below its root the run is (0: a root), as the journal names it: a child's budget is its root's taken down
    # this many levels (ADR-RUN-01 D4, ``RunBudget.of_claim``). A journal that names none: a root.
    depth: int = 0
    # The store's clock when it granted this claim, epoch ms: ``deadline_at_ms`` and every ``notBefore`` are on that
    # clock, never this node's (``store_time``, CH-14). ``None``: a journal that names none.
    store_now_ms: int | None = None

    @property
    def token(self) -> AttemptToken:
        return AttemptToken(self.run_id, self.attempt_id, self.fence)

    def store_time(self, clock: Clock, *, sent_at: float) -> StoreTime:
        """The store's clock for this attempt, carried from the claim on ``clock``'s suspend-aware reading
        ``sent_at``. The claim was asked at ``sent_at`` and answered later, so the carried clock runs at most one
        round trip ahead of the store's: a deadline is met that much early, never late."""
        return StoreTime(clock, store_ms=self.store_now_ms, at=sent_at)

    def journal_accepts(self, feature: str) -> bool:
        return self.accepts is None or feature in self.accepts


@dataclass(frozen=True)
class LeaseGrant:
    lease_ms: int


class Checkpoint(BaseModel):
    """Opaque fold snapshot; valid only under the rules in ``kernel.fold``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    stream_id: str
    fold_version: int
    seq: int
    state: dict[str, JsonValue]


# LiveBus buffer per run: bounded (about 5 minutes of 40 ms frames) and expiring,
# so a finished or abandoned run's deltas never outlive their use (REPORT 6.2.4).
LIVE_BUFFER_FRAMES = 8_000
LIVE_TTL_S = 3_600


@dataclass(frozen=True)
class LiveFrame:
    """A best-effort live frame; the projector drops frames from a lower fence."""

    run_id: str
    fence: int
    model_attempt: int
    n: int
    # The command kinds and ``followups`` go only to the activity bus (UX5), never the chat's.
    kind: Literal[
        "text_delta",
        "reasoning_delta",
        "tool_call",
        "command_started",
        "command_output",
        "command_finished",
        "followups",
    ]
    data: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    @property
    def frame_id(self) -> str:
        return f"d:{self.run_id}:{self.fence}:{self.model_attempt}:{self.n}"


class JournalError(Exception):
    """Base class for journal failures."""


class OwnershipLost(JournalError):
    """Stale fence, expired lease or the run left ``running`` (HTTP 404/409)."""


class JournalUnavailable(JournalError):
    """Transient failure; retry the same command until the lease runs out."""


class CommitRejected(JournalError):
    """The commit is invalid; nothing was written and a retry cannot succeed."""


class WriterNotAllowed(CommitRejected):
    pass


class EventRejected(CommitRejected):
    """Unknown event type or a payload that fails its schema."""


class CommandConflict(CommitRejected):
    """A ``commandId`` was reused for a different command."""


class RunNotClaimable(JournalError):
    pass


class InteractionNotOpen(JournalError):
    pass


class ConversationGrantNotOffered(JournalError):
    """A decision asked to hold for the conversation when the worker did not offer it (UX1). Nothing was written."""


class EditNotOffered(JournalError):
    """An approval with edited arguments the worker did not offer editing for (AP-06). Nothing was written."""


class InteractionResolved(JournalError):
    """A suspend for approval refused: every interaction the run asked for is
    already decided (Node resolved one while the worker held it). Nothing was
    written; the worker reads the decisions and goes on."""


class InputPending(JournalError):
    """A completion refused: the user queued input since the worker last read
    the journal (steering, REPORT D19). Nothing was written; the run takes it."""


class ContentMissing(JournalError):
    """A referenced payload is gone or fails its hash/size check (``journal_payload_missing``)."""


__all__ = [
    "APPROVAL_REASONS",
    "APPROVAL_RULE_GRANTS",
    "APPROVAL_EDITS",
    "APPROVAL_ARGS_DIGEST",
    "APPROVAL_EDIT_ORDER",
    "PRIORITY_PREEMPTION",
    "OUTCOME_WORDS",
    "PROVENANCE_RECORDS",
    "REPOSITORY_FETCHES",
    "SKIPPED_INPUTS",
    "SITE_UPLOAD_ASKS",
    "EditNotOffered",
    "CONNECT_PAUSES",
    "EXTERNAL_POLICY_ASKS",
    "KNOWLEDGE_EXPORTS",
    "SKILL_LABELS",
    "APPROVAL_RULE_LAYERS",
    "COMMAND_ACTIVITY",
    "CONTEXT_COMPACTED",
    "CONTEXT_COMPACTED_AUTO",
    "FOLLOWUPS",
    "CHILD_GRANTS",
    "CHILD_MCP_PINS",
    "CHILD_TASKS",
    "CHILD_LABELS",
    "TERMINAL_STATUSES",
    "AttemptToken",
    "Cancelled",
    "Checkpoint",
    "ChildResultRef",
    "Claim",
    "CommandConflict",
    "CompactAsk",
    "CommitReceipt",
    "CommitRejected",
    "CommitRequest",
    "Complete",
    "CompletedOutcome",
    "ContentMissing",
    "ContentRef",
    "EventRejected",
    "Fail",
    "InteractionChannel",
    "InteractionDecision",
    "InputArtifactRef",
    "InputPending",
    "ConversationGrantNotOffered",
    "InteractionNotOpen",
    "InteractionResolved",
    "JournalError",
    "JournalEvent",
    "JournalUnavailable",
    "LeaseGrant",
    "LIVE_BUFFER_FRAMES",
    "LIVE_TTL_S",
    "LiveFrame",
    "NewEvent",
    "OwnershipLost",
    "Quarantine",
    "RunNotClaimable",
    "RunSpec",
    "RunStatus",
    "Suspend",
    "SuspendReason",
    "Transition",
    "Visibility",
    "Writer",
    "WriterNotAllowed",
    "Yield",
    "YieldReason",
    "command_id",
]


def db_timestamp(now: datetime) -> str:
    """RFC 3339 UTC with milliseconds and a literal ``Z`` (envelope ``ts``)."""
    now = now.astimezone(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
