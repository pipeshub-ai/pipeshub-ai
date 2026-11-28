"""The durable kernel: resume by cursor, commit by the rules, recover effects by class.

REPORT 6.1.4 (commit rules), 6.2.2 (cursor dispatch, pre-run steps), 6.2.3
(crash windows, batch barrier, synthetic outcomes), 6.1.5 (self-fence) and
6.4 (raw provider state). One ``DurableKernel`` serves many claims; each
``run_claimed`` call works on one attempt.

Commit rules as implemented:

* ``model.response`` (with ``rawRef``) is durable before any dispatch; the user
  message, ``turn.started``, ``model.request`` and slot updates ride with it.
* The first attempt's capability record rides with the run's first commit:
  nothing the run does before that commit depends on it.
* A ``write``/``execute`` intent is its own fenced commit before the effect, and
  its outcome its own commit after. ``read``/``workspace`` intents ride with
  their outcomes, which all commit before the batch moves on.
* Suspension, yield and every terminal transition are their own commits.
* Approval (K-H, P2.6c): with an interaction wakeup, calls the policy asks
  about are requested in one commit with no transition (approval before
  intent: nothing about the call is journaled until it is approved), then held
  up to ``approval_hold_s`` while Node resolves them into the running run; an
  undecided call suspends the run. Node refuses a suspend with nothing left
  open (``InteractionResolved``), which closes the race with a decision
  journaled as the hold ends. Without a wakeup the request and the suspension
  are one commit, as before.
* Steering (D19): input the user queued is consumed at the boundary before the
  next model call, in its own commit, so the transcript shows it before the
  turn that reads it. A turn that would end the run with input queued becomes
  a follow-up turn instead; Node refuses a completion while input is queued
  (``InputPending``), which closes the race with a message queued mid-commit.

Within a batch, reads run concurrently and effects run one at a time in
``toolCalls`` order; results are rendered in ``toolCalls`` order regardless.

The journal is read to load and when a receipt leaves a gap (another writer
committed in between); a contiguous receipt is the head as of that commit, so
the next boundary does not read it again.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, NoReturn, cast

from pydantic import JsonValue

from harness_core.access.spend import input_estimate
from harness_core.budget.errors import BudgetRefused, BudgetUnavailable
from harness_core.budget.run_budget import RunBudget, budget_scope
from harness_core.context.compiler import compacted_history, compile_context
from harness_core.context.labels import labels_json, range_labels
from harness_core.context.ladder import Reduction, forced_compaction, plan_reduction
from harness_core.context.notes import (
    NOTES_SLOT,
    held,
    journaled_notes,
    new_notes,
    note_message,
)
from harness_core.context.ports import CompactionRefused, CompactionRequest
from harness_core.context.results import (
    RESULT_CAP_CHARS,
    RESULT_CAP_FLOOR,
    RESULT_CAP_SLOT,
    RESULT_FOOTERS_SLOT,
)
from harness_core.context.tools import TOOL_ORDER_SLOT
from harness_core.context.types import (
    Compaction,
    CompilerInput,
    HistoryItem,
    HostBands,
    SeqSpan,
)
from harness_core.context.user_compact import (
    USER_COMPACT_KEEP_TURNS,
    USER_COMPACT_SLOT,
    CompactReport,
    compacted_event,
)
from harness_core.context.wrap_up import fit_wrap_up
from harness_core.contracts import canonical_digest, canonical_json
from harness_core.contracts.generated.enums import CUT_SHORT_REASONS
from harness_core.effects.ask_reason import card_reason
from harness_core.effects.edits import (
    EDIT_UNREADABLE,
    ApprovedEdit,
    approved_edit,
    offering_edit,
)
from harness_core.effects.edits import (
    noted as with_edit_note,
)
from harness_core.effects.flow import (
    GrantingLedger,
    GrantsContext,
    edition_features,
    reason_for_journal,
)
from harness_core.effects.journal import JournalEffectLog
from harness_core.effects.reconcile import ReconcileEvidence, stamped_effect
from harness_core.effects.redaction import POLICY_REDACTION
from harness_core.effects.results import put_ref
from harness_core.effects.runner import MAX_EFFECT_RETRIES, EffectRunner
from harness_core.effects.types import (
    ALLOW_WITH_REDACTION,
    EffectCall,
    approval_denial,
    runs_unasked,
)
from harness_core.engines.outcome.outcome import Delivered, Failed
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.failpoints import afailpoint
from harness_core.gates.instructions import gate_notes
from harness_core.gates.plan import (
    PLAN_DECLINED_ANSWER,
    PLAN_RULE,
    PlanGate,
    plan_gated,
    plan_tools,
)
from harness_core.gates.spec import dry_run, plan_approval, verify_gate
from harness_core.gates.verify import (
    VERIFY_SLOT,
    CheckResult,
    RunVerifier,
    Verification,
    VerifyPorts,
    verify_round,
)
from harness_core.interactions.approval_request import approval_request
from harness_core.interactions.connect import ConnectGate, ConnectNeed, skipped_connects
from harness_core.interactions.hold import APPROVAL_HOLD_S
from harness_core.interactions.journal import POLL_S, JournalInteractionBridge, said_in
from harness_core.interactions.ports import InteractionTicket, RunBinding
from harness_core.invoker import InvocationScope, Invoker
from harness_core.journal.children import (
    ChildRefusal,
    ChildRefused,
    JoinReady,
    child_result_document,
    run_workspace,
)
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS, CommitUnit, pack
from harness_core.journal.ports import SealingContentStore
from harness_core.journal.rejected import COMMIT_REJECTED, sealed_completion
from harness_core.journal.types import (
    APPROVAL_ARGS_DIGEST,
    APPROVAL_EDIT_ORDER,
    APPROVAL_EDITS,
    APPROVAL_REASONS,
    APPROVAL_RULE_GRANTS,
    APPROVAL_RULE_LAYERS,
    CHILD_GRANTS,
    CHILD_LABELS,
    CHILD_MCP_PINS,
    CHILD_TASKS,
    CONNECT_PAUSES,
    CONTEXT_COMPACTED,
    CONTEXT_COMPACTED_AUTO,
    EXTERNAL_POLICY_ASKS,
    INTENT_ALLOWED_BY,
    PRIORITY_PREEMPTION,
    Cancelled,
    ChildResultRef,
    CommitRejected,
    CommitRequest,
    Complete,
    ContentMissing,
    Fail,
    InputPending,
    InteractionResolved,
    JournalUnavailable,
    LiveFrame,
    NewEvent,
    OwnershipLost,
    Quarantine,
    Suspend,
    Yield,
    YieldReason,
    command_id,
    db_timestamp,
)
from harness_core.kernel.context.errors import ContextBudgetExceeded
from harness_core.kernel.core.messages import (
    AssistantMessage,
    Message,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
    add_loop_note,
)
from harness_core.kernel.core.stall import RoundCall, StallState, next_stall, stalled
from harness_core.kernel.core.tokens import count_tokens
from harness_core.kernel.core.turn_limit import (
    CutShort,
    cut_short_fallback,
    cut_short_note,
    degraded_answer,
)
from harness_core.kernel.durable.capability import (
    CAPABILITY_LOST,
    TOOL_WITHDRAWN,
    capability_changes,
    count_capability_waits,
    recheck_delay,
    returned,
    withdrawn_servers,
)
from harness_core.kernel.durable.context_compiler import (
    compile_session,
    default_bands,
    journal_context,
    latest_measurement,
)
from harness_core.kernel.durable.delegation import (
    Delegated,
    DelegatedState,
    DelegationView,
    Gathered,
    Refuse,
    Wait,
)
from harness_core.kernel.durable.director import (
    SYNTHETIC_PROVIDER,
    SYNTHETIC_TRANSPORT,
    is_synthetic,
    synthetic_turns,
)
from harness_core.kernel.durable.effects import UNKNOWN_TOOL, ToolEffect
from harness_core.kernel.durable.harness_channel import HARNESS_ROLE, harness_block
from harness_core.kernel.durable.lease import (
    JournalGone,
    Lease,
    LeaseKeeper,
    LeaseLost,
    while_leased,
)
from harness_core.kernel.durable.ports import (
    REQUEST_TOO_LARGE,
    ModelBackoff,
    ModelCall,
    ModelFailed,
    RunEnding,
    SampledTurn,
    SteeringInput,
    TerminalTools,
    ToolContext,
    ToolExecution,
    TurnPlan,
    TurnResult,
    allow_all,
)
from harness_core.kernel.durable.provider_wait import PROVIDER_WAIT_SLOT, ProviderWaits
from harness_core.kernel.durable.reconcile import (
    ABANDONED_ERROR,
    OPERATOR_ABANDONED,
    OPERATOR_APPLIED,
    OPERATOR_EVIDENCE,
    ReconcileSource,
    applied_execution,
    is_unknown_interaction,
    operator_verdict,
    reconciled_event,
    unknown_effect_for,
    with_recall_rule,
)
from harness_core.kernel.durable.routing import RoutedModelPort
from harness_core.kernel.durable.slots import (
    BUDGET_SCOPE_SLOT,
    CAPABILITY_SLOT,
    CUT_SHORT_SLOT,
    FINAL_ANSWER_SLOT,
    POST_RUN_SLOT_PREFIX,
    PRE_RUN_SLOT_PREFIX,
    TERMINAL_SLOT,
    WRAP_UP_FIT_SLOT,
)
from harness_core.kernel.durable.tool_lists import SessionToolLists
from harness_core.kernel.durable.transcript import (
    TranscriptRenderer,
    decode_assistant,
    decode_result,
    encode_assistant,
    encode_user_parts,
)
from harness_core.kernel.fold import (
    FOLD_VERSION,
    FoldIncompatible,
    InteractionPurpose,
    Invocation,
    QueuedInput,
    RunState,
    SessionState,
    checkpoint_is_valid,
    fold_run,
    fold_session,
    interaction_id,
    is_connect_interaction,
    make_checkpoint,
    restore_checkpoint,
)
from harness_core.kernel.hooks.builtin.truncation_recovery import truncated_call_note
from harness_core.logs import HarnessLog
from harness_core.models.output_bound import shortened
from harness_core.models.policy import (
    RETRIED,
    FallbackTrigger,
    ModelPolicy,
    ModelPolicyConfig,
    PolicyRoute,
    classify_failure,
)
from harness_core.priority import PreemptRequests
from harness_core.security.labels import LabelsContext
from harness_core.security.secrets import NO_SECRETS
from harness_core.telemetry import genai
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.ports import RunRef
from harness_core.tools.call_tool import CALL_TOOL
from harness_core.workspace.events import workspace_committed
from harness_core.workspace.forks import JoinedChild
from harness_core.workspace.types import WorkspaceStoreUnavailable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence

    from harness_core.clock import Clock
    from harness_core.context.ports import CompactionSummary
    from harness_core.context.types import CompiledContext
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.contracts.generated.enums import ToolErrorCode
    from harness_core.effects.reconcile import Reconciler
    from harness_core.interactions.ports import InteractionDecision, InteractionWakeup
    from harness_core.journal.children import ChildAdmitted, ChildRequest
    from harness_core.journal.ports import (
        CheckpointPort,
        ChildRunPort,
        ContentStorePort,
        JournalPort,
        LiveSinkPort,
    )
    from harness_core.journal.types import (
        Claim,
        CommitReceipt,
        JournalEvent,
        Transition,
    )
    from harness_core.kernel.core.responses import TokenUsage
    from harness_core.kernel.core.tool_schema import ToolSchema
    from harness_core.kernel.durable.audience import HistoryAudience
    from harness_core.kernel.durable.context_compiler import ContextCompiler
    from harness_core.kernel.durable.delegation import DelegationTools
    from harness_core.kernel.durable.harness_channel import HarnessInstruction
    from harness_core.kernel.durable.ports import (
        AfterRunStep,
        ApprovalPolicy,
        CapabilityCheck,
        ChildResultShaper,
        DeltaKind,
        DurableModelPort,
        DurableToolPort,
        ImportedHistory,
        InputRenderer,
        LoopDirector,
        PostRunStep,
        PreRunGate,
        PreRunStep,
        RenderedInput,
        ResponseReview,
        RunEndingOutcome,
        RunPresenter,
        RunRestore,
        TurnHook,
        TurnShaper,
        WrapUpPort,
    )
    from harness_core.models.stream_watch import CallTiming
    from harness_core.security.labels import SourceLedger
    from harness_core.security.secrets import CallSecrets
    from harness_core.telemetry.ports import HarnessTracer, SpanHandle
    from harness_core.workspace.changes import FileChangeKind
    from harness_core.workspace.forks import ChildMergeReport
    from harness_core.workspace.ports import ChildWorkspaces

MAX_ATTEMPTS = 3
STALE_READ_RETRIES = 3
# A model call the run's budget refused for want of spend (BUDGETEND): the run answers from what it has.
BUDGET_STOPS = frozenset({"budget_exhausted", "budget_insufficient"})
# What a completion adds to the outcomes it commits with: the turn's end, the answer, the finalizer's events.
ENDING_RESERVE = 8
# The most a response's commit holds before its presentation (the input, the turn's start, slots, the
# request and the response): a presentation that leaves this much room was committed with it.
HEAD_RESERVE = 16
# The run's error type when the journal moved it to a route this worker cannot build (P4.7).
MODEL_ROUTE_UNAVAILABLE = "model_route_unavailable"
# Live deltas are coalesced into one frame per window (REPORT 6.2.4: 30-50 ms).
LIVE_WINDOW_S = 0.04


DispositionStatus = Literal[
    "completed",
    "suspended",
    "yielded",
    "failed",
    "cancelled",
    "quarantined",
    "released",
    "lost",
]
_Mode = Literal[
    "run",
    "retry",
    "ask",
    "deny",
    "reject",
    "unknown",
    "waiting",
    "spawn",
    "joining",
    "joined",
    # P2.9: ask the reconciler; re-call with a fresh intent; an operator's "applied"; abandon the run.
    "reconcile",
    "recall",
    "confirm",
    "abandon",
    # P4.17: a call of a truncated turn: its arguments are incomplete, so it gets an error result, never runs.
    "cut",
    # R4: a call to a tool the session's list shows that the run no longer has, or whose arguments changed.
    "stale",
    # G-12: the call needs an item its run's owner has not connected; the run waits for them to connect it.
    "connect",
    # G-12: connected while this attempt's tools were assembled: the attempt yields so the next one loads it.
    "reassemble",
]
_MACHINE = re.compile(r"[^a-z0-9_.-]")
_MODEL_NAME = re.compile(r"[^A-Za-z0-9_-]")
_TRUSTED: dict[str, JsonValue] = {"integrity": "trusted"}


@dataclass(frozen=True)
class Disposition:
    status: DispositionStatus
    reason: str | None = None
    transcript: tuple[Message, ...] = ()


class _Halt(Exception):
    def __init__(self, disposition: Disposition) -> None:
        super().__init__(disposition.status)
        self.disposition = disposition


class _CutShort(Exception):
    """A limit ran out mid-turn (the budget refused a call or a compaction): the run takes its answer-only turn."""

    def __init__(self, reason: CutShort) -> None:
        super().__init__(reason)
        self.reason: CutShort = reason


class _TooLarge(Exception):
    """The provider refused the compiled request as larger than its window (P4.9): reduce and resend."""


@dataclass(frozen=True)
class _Read:
    """What one read journals, and whether its result ends the run (a question, a terminal answer)."""

    unit: CommitUnit
    ends: bool


@dataclass
class _Planned:
    inv: Invocation
    call: ToolCall
    effect: ToolEffect
    mode: _Mode
    known_tool: bool = True
    # A ``stale`` call's error result: the text and its code.
    refusal: tuple[str, ToolErrorCode] | None = None
    # A ``connect`` call's item (G-12).
    need: ConnectNeed | None = None


def _tool_error(execution: ToolExecution) -> str | None:
    """An ``execute_tool`` span's ``error.type``: the call's error code, its status when it has none."""
    if execution.status == "ok":
        return None
    return str(execution.error_code) if execution.error_code is not None else execution.status


def _purpose(mode: str) -> InteractionPurpose:
    if mode == "ask":
        return "approve"
    return "connect" if mode == "connect" else "unknown"


def _journal_usage(usage: TokenUsage) -> dict[str, JsonValue]:
    journaled: dict[str, JsonValue] = {
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "cacheReadTokens": usage.cache_read_tokens,
        "cacheWriteTokens": usage.cache_write_tokens,
    }
    # Events from before every transport counted the whole prompt carry no marker, and a usage with no cached
    # tokens reads the same either way: the marker goes where a reader has something to tell apart.
    if usage.cache_read_tokens or usage.cache_write_tokens:
        journaled["inputIncludesCached"] = True
    return journaled


def _context_tokens(
    items: Sequence[HistoryItem], compactions: Sequence[Compaction], cleared: Sequence[SeqSpan]
) -> int:
    """An estimate of the history as the next request would hold it: what ``/compact`` removed is the difference."""
    compiled = compile_context(
        CompilerInput(host=HostBands(), history=tuple(items), compactions=tuple(compactions), cleared=tuple(cleared))
    )
    return count_tokens(list(compiled.history))


def _machine_name(value: str) -> str:
    cleaned = _MACHINE.sub("-", value.lower())[:64]
    return cleaned if cleaned and cleaned[0].isalnum() else f"x{cleaned}"[:64]


def _timing_fields(timing: CallTiming | None) -> dict[str, int]:
    """The answering attempt's progress on ``model.response`` (P4.10): a slow step says whether the model was
    slow to start, went silent midway, or was simply long."""
    if timing is None:
        return {}
    return {
        "firstEventMs": round(timing.first_event_s * 1000),
        "maxGapMs": round(timing.max_gap_s * 1000),
        "durationMs": round(timing.duration_s * 1000),
        "events": timing.events,
    }


def _error_type(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]", "_", value.lower())[:63]
    return cleaned if cleaned[:1].isalpha() else f"e{cleaned}"[:64]


def _model_tool_name(value: str) -> str:
    return _MODEL_NAME.sub("_", value)[:64] or "invalid"


# How often a call in flight re-reads the journal for a cancel no wake announced (UX3).
CANCEL_POLL_S = 5.0


_DETACHED: set[asyncio.Task[None]] = set()


def _spawn_detached(work: Coroutine[Any, Any, None]) -> None:
    """A task that outlives the attempt that started it, held until it ends (asyncio keeps only weak references)."""
    task = asyncio.get_running_loop().create_task(work)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)


async def settle_after_run() -> None:
    """Waits for the after-run work this process started (tests, and a worker draining before it exits)."""
    _ = await asyncio.gather(*list(_DETACHED), return_exceptions=True)


# The agent a loop run's ``invoke_agent`` span names (an engine run names its engine).
LOOP_AGENT = "loop"
_FAILED_DISPOSITIONS = frozenset({"failed", "quarantined", "lost"})


@dataclass
class DurableKernel:
    journal: JournalPort
    content: ContentStorePort
    model: DurableModelPort
    tools: DurableToolPort
    clock: Clock
    checkpoints: CheckpointPort | None = None
    live: LiveSinkPort | None = None
    approval: ApprovalPolicy = allow_all
    # What the run's session has read (P2.5a): fed every event this kernel folds (SEC1's flow rule reads it).
    ledger: SourceLedger | None = None
    # What the host journals as the ledger shows the conversation holding more (an edition's run fields); rides the
    # next commit.
    level_events: Callable[[], Sequence[NewEvent]] | None = None
    # Set: approvals are held in the running run (K-H); unset: asking suspends at once.
    interactions: InteractionWakeup | None = None
    # Set: a call whose item its owner has not connected waits for them to connect it (G-12); unset, it fails.
    connect: ConnectGate | None = None
    # Set: a model call or a batch of reads in flight is raced against a cancel (UX3); unset, cancel waits for the step.
    cancel_wakeup: InteractionWakeup | None = None
    cancel_poll_s: float = CANCEL_POLL_S
    approval_hold_s: float = APPROVAL_HOLD_S
    interaction_poll_s: float = POLL_S
    pre_run: Sequence[PreRunStep] = ()
    post_run: Sequence[PostRunStep] = ()
    after_run: Sequence[AfterRunStep] = ()
    # Where after-run work is started (the driver keeps its tasks); unset, a task of this process.
    spawn: Callable[[Coroutine[Any, Any, None]], None] = field(default_factory=lambda: _spawn_detached)
    pre_run_gate: PreRunGate | None = None
    turn_hook: TurnHook | None = None
    capability_check: CapabilityCheck | None = None
    # Awaited with the names a turn calls before any is resolved: a tool built at its source's first use (an MCP
    # server listed then, D-LAZY-4) exists, with its real effect, before the call is planned. Unset: nothing to build.
    # Answers each name that stands for another (a server's own name for its tool) with the name it runs under.
    prepare_calls: Callable[[Sequence[str]], Awaitable[Mapping[str, str] | None]] | None = None
    # The first-use pins of the run's MCP servers it has listed so far (D-LAZY-5), per instance: carried into every
    # child it spawns, which is pinned to them and does not list those servers again (R31HOT2). Unset: none.
    mcp_pins: Callable[[], Mapping[str, Mapping[str, str]]] | None = None
    presenter: RunPresenter | None = None
    shaper: TurnShaper | None = None
    review: ResponseReview | None = None
    # What the run's verify gate (G-05) may look at; ``None``: every check that needs a port fails closed.
    verify: VerifyPorts | None = None
    restore: RunRestore | None = None
    inputs: InputRenderer | None = None
    history: ImportedHistory | None = None
    # A shared session's run (SS2): whose turns its model sees; unset, all of them.
    audience: HistoryAudience | None = None
    # Child runs (P6.1): Node's admission, and which tool calls delegate to it.
    children: ChildRunPort | None = None
    delegation: DelegationTools | None = None
    # A loop strategy's phases (K-L): what it injects or takes before each turn.
    director: LoopDirector | None = None
    # A child run's result as its product completes it (K-L).
    child_result: ChildResultShaper | None = None
    # A joined child's workspace fork offered to this run's workspace (P6.4).
    child_workspaces: ChildWorkspaces | None = None
    # The context compiler (P4.1/P4.2): unset, requests are assembled as before.
    context: ContextCompiler | None = None
    # What summarises for the person's ``/compact`` when ``context`` is unset (SLASH2 C2): its strategy and ladder
    # policy only; requests are still assembled as before, with the journaled compaction applied.
    compactor: ContextCompiler | None = None
    # With ``context`` unset, ``compactor`` also takes the ladder's compact rung on its own (LC2FIX-2): a shaped
    # request past ``compact_above`` of the window is compacted before it is sent again. No clearing rung: only
    # the compiled layout applies a cleared span.
    auto_compact: bool = False
    # Opt-in per tool (P2.9, ``HARNESS_RECONCILE``): unset, an unknown effect goes to a person as before.
    reconciler: Reconciler | None = None
    # The run's wrap-up reserve (BUDGETEND): held before the working phase spends, drawn by the answer-only turn.
    wrap_up: WrapUpPort | None = None
    # Retries and fallback over the run's pinned routes (P4.7); ``model`` is then a
    # ``RoutedModelPort``. Unset: a model failure is handled as before.
    model_policy: ModelPolicyConfig | None = None
    # What the attempt's tool connections resolved: masked exactly in every call's outcome (SEC4).
    secrets: CallSecrets = NO_SECRETS
    system: str | None = None
    live_window_s: float = LIVE_WINDOW_S
    lease_margin_s: float = 2.0
    renew_interval_s: float | None = None
    max_attempts: int = MAX_ATTEMPTS
    # The journal's limit (the contract's); a step that journals more is committed in parts.
    max_commit_events: int = MAX_COMMIT_EVENTS
    max_effect_retries: int = MAX_EFFECT_RETRIES
    inline_backoff_s: float = 0.0
    yield_before_effects: bool = False
    journal_retry_s: float = 0.2
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # G-45: the attempts the worker's claimer asked to give their slot to a higher class; unset, never preempted.
    preempt: PreemptRequests | None = None
    # P9 / OB-01: the run's ``invoke_agent`` span and its ``chat`` and ``execute_tool`` spans; no-op by default.
    tracer: HarnessTracer = NOOP_TRACER
    _drain: bool = field(default=False, init=False)

    def request_drain(self) -> None:
        """SIGTERM: yield at the next boundary instead of starting new work."""
        self._drain = True

    @property
    def draining(self) -> bool:
        return self._drain

    async def run_claimed(
        self, claim: Claim, *, claim_sent_at: float | None = None
    ) -> Disposition:
        sent_at = self.clock.suspend_aware() if claim_sent_at is None else claim_sent_at
        with self.tracer.span(
            f"{genai.INVOKE_AGENT} {LOOP_AGENT}",
            kind="internal",
            run=RunRef(claim.org_id, claim.run_id, claim.session_id, claim.attempt_id, claim.fence),
            attributes={
                genai.OPERATION: genai.INVOKE_AGENT,
                genai.AGENT_NAME: LOOP_AGENT,
                genai.CONVERSATION_ID: claim.session_id,
            },
        ) as span:
            # ADR-RUN-01 D4: the run's tool calls are bounded by its budget's deadline (``ToolTimeBounds.wall_for``).
            attempt = _ClaimedRun(self, claim, sent_at)
            with budget_scope(RunBudget.of_claim(claim), attempt.store_time.now_s):
                disposition = await attempt.execute()
            span.set({"pipeshub.disposition": disposition.status, "pipeshub.reason": disposition.reason})
            if disposition.status in _FAILED_DISPOSITIONS:
                span.end(error_type=disposition.reason or disposition.status)
            return disposition


class _LeasedContent:
    """The content port as an attempt uses it: an unreachable Node (a restart, a
    replacement behind its balancer) is retried like a journal read while the
    lease holds, then the run is handed back (``JournalGone``), never failed."""

    def __init__(self, run: _ClaimedRun) -> None:
        self._run = run

    async def put(self, data: bytes) -> ContentRef:
        # A put whose answer was lost leaves an unreferenced sealed blob; retention collects it (T5).
        return await self._leased(lambda: self._run.k.content.put(data), "put")

    async def get(self, ref: ContentRef) -> bytes:
        return await self._leased(lambda: self._run.k.content.get(ref), "get")

    async def put_sealed(self, data: bytes) -> ContentRef | None:
        """``data`` kept out of its ref; ``None`` when the store has one way of keeping content only."""
        store = self._run.k.content
        if not isinstance(store, SealingContentStore):
            return None
        return await self._leased(lambda: store.put_sealed(data), "put")

    async def _leased[T](self, call: Callable[[], Awaitable[T]], operation: str) -> T:
        run = self._run
        return await while_leased(
            call,
            lease=run.lease,
            retry_s=run.k.journal_retry_s,
            sleep=run.k.sleep,
            on_retry=lambda retries: run.log.warning(
                "content.retry",
                operation=operation,
                retries=retries,
                retryS=run.k.journal_retry_s,
            ),
        )


class _ClaimedRun:
    def __init__(self, kernel: DurableKernel, claim: Claim, sent_at: float) -> None:
        self.k = kernel
        self.claim = claim
        # After-run steps prepared for the terminal commit in flight (UX5).
        self._after: list[tuple[AfterRunStep, object]] = []
        self.lease = Lease(
            kernel.clock,
            sent_at=sent_at,
            lease_ms=claim.lease_ms,
            margin_s=kernel.lease_margin_s,
        )
        self.store_time = claim.store_time(kernel.clock, sent_at=sent_at)
        self.run = RunState(run_id=claim.run_id)
        self.session = SessionState(stream_id=claim.stream_id)
        self.content = _LeasedContent(self)
        self.renderer = TranscriptRenderer(
            self.content, kernel.model, kernel.history, kernel.audience, claim.run_id
        )
        self.log = HarnessLog.for_claim("harness_core.kernel.durable", claim)
        # G-04: the spec's plan gate, read from this run's journal whenever it decides.
        self.plan_gate = PlanGate(lambda: self.run) if plan_approval(claim.spec) else None
        self.tools = plan_tools(kernel.tools, self.plan_gate)
        self.approval = plan_gated(kernel.approval, self.plan_gate)
        # G-05: the spec's verify gate, over what the host lets it look at.
        verifying = verify_gate(claim.spec)
        self.verifier = RunVerifier(verifying, kernel.verify or VerifyPorts()) if verifying is not None else None
        self.tool_lists = SessionToolLists(self._put, self.renderer.blob, self.tools, self.log)
        self.effect_log = JournalEffectLog(
            self._commit_whole, self.content, lambda: self.run, allowed_by=claim.journal_accepts(INTENT_ALLOWED_BY)
        )
        self.effects = EffectRunner(
            self.effect_log,
            self.approval,
            max_retries=kernel.max_effect_retries,
            guard=self.lease.check,
            propagate=(LeaseLost, OwnershipLost),
            logs=self.log,
            secrets=kernel.secrets,
            dry_run=dry_run(claim.spec),
        )
        self.invoker = Invoker(
            self.tools,
            self.effects,
            InvocationScope(claim.run_id, claim.org_id, claim.attempt_id),
            logs=self.log,
        )
        self.bridge = (
            JournalInteractionBridge(
                self,
                kernel.interactions,
                kernel.clock,
                kernel.interaction_poll_s,
            )
            if kernel.interactions is not None
            else None
        )
        self._commits = 0
        # The turns whose response this attempt journaled, presentation included.
        self._presented: set[int] = set()
        self._model_attempt = 0
        # The answer text streamed by the model call in flight: what a Stop keeps (UX3).
        self._streamed: list[str] = []
        # The per-result cap the last compiled request was cut to (P4.9: what a too-large refusal halves).
        self._result_cap: int | None = None
        # The answer-only turn past maxTurns this attempt is taking (J-15), if any.
        self._final_turn: int | None = None
        # Which limit that turn is for (BUDGETEND).
        self._cut_short: CutShort | None = None
        # Which shared budget stopped the run (WP-51), when a budget stop said so; the answer names it.
        self._budget_scope: str | None = None
        # The stall state through ``_stall_turn`` (BU-06), recomputed from the journal by each attempt.
        self._stall, self._stall_turn = StallState(), 0
        self._needs_input = False
        self._turn_text = ""
        self._turn_cut: str | None = None
        self._answers: dict[str, tuple[str, str | None]] = {}
        # Invocations planned under ``allow_with_redaction``: their results are redacted when they run.
        self._redacting: set[str] = set()
        # Invocations a person approved with changed arguments (AP-06): what runs, and what the model is told ran.
        self._edited: dict[str, dict[str, JsonValue]] = {}
        # By invocation: the name a ``call_tool`` wrapper named, whose digest the invocation is journaled under.
        self._called_as: dict[str, str] = {}
        # The connect waits this attempt saw open: its tools were assembled before the owner connected (G-12).
        self._open_connects: set[str] = set()
        # Asks this attempt left to the next plan once a conversation grant answered them (FLOWGRANT D3), each once.
        self._granted_before_ask: set[str] = set()
        self._live_failed = False
        self._lock = asyncio.Lock()
        # Events that ride with the next commit (the capability record).
        self._riding: list[NewEvent] = []
        # The last round trip folded the journal head: the next boundary need not read.
        self._at_head = False
        # This run's events as folded, when the fold started at the stream's start.
        self._run_events: list[JournalEvent] | None = None
        # Joined children's merges this attempt already asked for (the store answers again anyway).
        self._merges: dict[str, ChildMergeReport | None] = {}
        # Reconcile verdicts that ride with the suspension that parks their effect.
        self._verdicts: dict[str, NewEvent] = {}
        # The routes are the pinned spec's, whatever the port was built with (P4.7).
        self._policy = (
            ModelPolicy.from_spec(claim.spec.model, kernel.model_policy)
            if kernel.model_policy is not None and isinstance(kernel.model, RoutedModelPort)
            else None
        )

    # ---- lifecycle ----

    async def execute(self) -> Disposition:
        keeper: asyncio.Task[None] | None = None
        try:
            await self._load()
            self.log.info(
                "run.resume",
                cursor=self.run.cursor.next_action,
                turn=self.run.cursor.turn,
                seq=self.run.seq,
                lastSeq=self.claim.last_seq,
                attempt=self.claim.attempt,
                pending=len(self.run.cursor.pending),
            )
            if self.claim.attempt > self.k.max_attempts:
                self.log.warning(
                    "run.quarantine",
                    reason="crash_loop",
                    attempt=self.claim.attempt,
                    maxAttempts=self.k.max_attempts,
                )
                return await self._terminate(
                    Quarantine(
                        attempts=self.claim.attempt, last_error_type="crash_loop"
                    ),
                    "quarantined",
                )
            if self.k.restore is not None:
                await self.k.restore.restore(await self._this_run_events())
            if self.k.renew_interval_s is not None:
                keeper = asyncio.create_task(
                    LeaseKeeper(
                        self.k.journal,
                        self.claim.token,
                        self.lease,
                        self.k.clock,
                        interval_s=self.k.renew_interval_s,
                        sleep=self.k.sleep,
                    ).run()
                )
            return await self._loop()
        except _Halt as halt:
            return halt.disposition
        except JournalGone:
            return await self._journal_gone()
        except _IncompatibleError as exc:
            self.log.warning("run.release", reason="fold_incompatible")
            await self.k.journal.release(self.claim.token, reason="fold_incompatible")
            return Disposition("released", f"fold_incompatible: {exc}")
        except (LeaseLost, OwnershipLost) as exc:
            if self.lease.self_fenced:
                self.log.warning("run.self_fence", reason="lease_expired_before_effect")
            else:
                # Node's refusal code names why (CONVERSATION_DELETED, STALE_ATTEMPT); never content.
                cause = str(exc) if isinstance(exc, OwnershipLost) else None
                self.log.warning("run.lease_lost", reason=type(exc).__name__, cause=cause)
            return Disposition("lost", str(exc))
        except CommitRejected as refused:
            # Refused for good (a 4xx validation): another attempt would repeat the run's work to be refused again.
            self.log.error("run.commit_rejected", refusal=type(refused).__name__, reason=str(refused)[:200])
            return await self._fail_safely(COMMIT_REJECTED, str(refused))
        except Exception as exc:
            self.log.error("run.kernel_error", exc=exc, errorType=type(exc).__name__)
            return await self._fail_safely("kernel_error", type(exc).__name__)
        finally:
            if keeper is not None:
                _ = keeper.cancel()
            if self.k.wrap_up is not None:
                await self.k.wrap_up.release()

    async def _load(self) -> None:
        if self.claim.spec.fold_version > FOLD_VERSION:
            await self._release("spec_fold_version")
        ref = self.claim.checkpoint_ref
        if self.k.checkpoints is not None and ref is not None:
            checkpoint = await self.k.checkpoints.load(ref)
            if checkpoint is not None and checkpoint_is_valid(
                checkpoint,
                run_id=self.claim.run_id,
                head_seq=self.claim.last_seq,
                ref=ref,
                referenced_ref=self.claim.checkpoint_ref,
            ):
                self.run, self.session = restore_checkpoint(checkpoint)
        if self.run.seq == 0:
            self._run_events = []
        elif self.k.ledger is not None:
            # A checkpoint skips the events before it; the ledger needs the session's whole stream.
            self.k.ledger.apply(await self._read())
        for _ in range(STALE_READ_RETRIES + 1):
            try:
                await self._sync()
            except _IncompatibleError as exc:
                await self._release(f"fold_incompatible: {exc}")
            if self.run.seq >= self.claim.last_seq:
                return
        await self._release("journal_behind_claim")

    async def _release(self, reason: str) -> None:
        self.log.warning("run.release", reason=_machine_name(reason.split(":", 1)[0]))
        await self.k.journal.release(self.claim.token, reason=reason)
        raise _Halt(Disposition("released", reason))

    async def _sync(self) -> None:
        self._fold(await self._read(after_seq=self.run.seq))
        self._at_head = True

    async def _read(self, *, after_seq: int = 0) -> list[JournalEvent]:
        """A read retries a transient outage while the lease holds, as a commit
        does; it gives up while the claim can still be released."""
        return await while_leased(
            lambda: self.k.journal.read(self.claim.stream_id, after_seq=after_seq),
            lease=self.lease,
            retry_s=self.k.journal_retry_s,
            sleep=self.k.sleep,
            on_retry=lambda retries: self.log.warning(
                "run.fold_retry",
                afterSeq=after_seq,
                retries=retries,
                retryS=self.k.journal_retry_s,
            ),
        )

    async def _this_run_events(self) -> tuple[JournalEvent, ...]:
        """This run's events as folded; read only when the fold started from a checkpoint."""
        if self._run_events is not None:
            return tuple(self._run_events)
        return tuple(e for e in await self._read() if e.run_id == self.claim.run_id)

    async def _journal_gone(self) -> Disposition:
        """Hand the run back: another attempt folds it once the journal answers."""
        try:
            await self.k.journal.release(self.claim.token, reason="journal_unavailable")
        except (JournalUnavailable, OwnershipLost) as exc:
            self.log.warning(
                "run.lease_lost", reason="journal_unavailable", errorType=type(exc).__name__
            )
            return Disposition("lost", "journal unavailable until the lease ran out")
        self.log.warning("run.release", reason="journal_unavailable")
        return Disposition("released", "journal_unavailable")

    def _fold(self, events: list[JournalEvent] | tuple[JournalEvent, ...]) -> None:
        try:
            run = fold_run(events, run_id=self.claim.run_id, state=self.run)
            session = fold_session(
                events, stream_id=self.claim.stream_id, state=self.session
            )
        except FoldIncompatible as exc:
            raise _IncompatibleError(str(exc)) from exc
        self.run, self.session = run, session
        if self.k.ledger is not None:
            self.k.ledger.apply(events)
            if self.k.level_events is not None:
                self._riding.extend(self.k.level_events())
        if self._run_events is not None:
            self._run_events.extend(e for e in events if e.run_id == self.claim.run_id)

    async def _verify_capabilities(self) -> Disposition | None:
        """The first attempt journals what it observed; a later one that observes
        something else must not run on it (REPORT 11, ``capability_lost``)."""
        check = self.k.capability_check
        if check is None or self.run.cursor.next_action in ("done", "suspended"):
            return None
        if self.run.cancel_requested:
            return None
        observed = dict(await check.observe())
        recorded = self.run.slots.get(CAPABILITY_SLOT)
        if recorded is None:
            self._riding.append(
                NewEvent(
                    "loop.state_saved",
                    {"slots": {CAPABILITY_SLOT: cast("JsonValue", observed)}},
                )
            )
            self.log.info("run.capabilities_recorded", count=len(observed))
            return None
        before = recorded if isinstance(recorded, dict) else {}
        back = returned(before, observed)
        if back:
            # The run never held these: they stay withheld until the next run offers them (WP79-FU-5).
            await check.withhold(cast("dict[str, JsonValue]", back))
            observed = dict(await check.observe())
            self.log.info("run.tools_withheld", reason="returned", keys=sorted(back))
        changed = capability_changes(before, observed)
        if not changed:
            self.log.info("run.capabilities_verified", count=len(observed))
            return None
        servers = withdrawn_servers(observed, changed)
        if servers:
            self.log.warning(
                "run.capability_lost", reason="tool_withdrawn", changed=changed, rechecks=0, outcome="failed"
            )
            return await self._terminate(
                Fail(TOOL_WITHDRAWN, PRESENTER.failure(Failed(TOOL_WITHDRAWN, Delivered(), servers=tuple(servers))), servers=tuple(servers)), "failed"
            )
        return await self._capability_lost("digest_mismatch", changed)

    async def _capability_lost(self, reason: str, changed: list[str]) -> Disposition:
        waited = count_capability_waits(await self._read(), self.claim)
        delay = recheck_delay(waited)
        if delay is not None:
            self.log.info(
                "run.capability_wait",
                reason=reason,
                changed=changed,
                recheck=waited + 1,
                delayS=delay,
            )
            return await self._yield("capability_wait", delay)
        self.log.warning(
            "run.capability_lost",
            reason=reason,
            changed=changed,
            rechecks=waited,
            outcome="failed",
        )
        return await self._terminate(Fail(CAPABILITY_LOST), "failed")

    async def _loop(self) -> Disposition:
        lost = await self._verify_capabilities()
        if lost is not None:
            return lost
        if self.run.responses:
            # The first attempt rendered the imported history before its first
            # call's tools ran; a resumed one may dispatch before any render.
            await self.renderer.prime(self.session)
        while True:
            if not self._at_head:
                await self._sync()
            self._at_head = False
            cursor = self.run.cursor
            action = cursor.next_action
            if action == "done":
                status = _terminal_disposition(self.run.status)
                self.log.info("run.done", status=status, outcome=self.run.outcome)
                transcript = (
                    await self.renderer.render(self.session)
                    if status == "completed"
                    else []
                )
                return Disposition(status, self.run.outcome, tuple(transcript))
            if self.run.cancel_requested:
                self.log.info("run.cancel", reason="cancel_requested")
                return await self._terminate(Cancelled(), "cancelled", "stopped")
            # An answer already journaled is finished, not yielded: completing
            # it is one commit and no model or tool call.
            if self.k.draining and action != "assemble":
                return await self._yield("drain")
            if action not in ("assemble", "suspended") and self._preempted():
                return await self._yield("preempted")
            if action == "suspended":
                if self.run.status == "suspended":
                    return Disposition("suspended")
                if self._waits_on_children_only():
                    joined = await self._suspend_join()
                    if joined is not None:
                        return joined
                else:
                    # Approvals first: a decided ask runs, then the batch joins its children.
                    if await self._hold():
                        continue
                    suspended = await self._suspend([])
                    if suspended is not None:
                        return suspended
            if action == "call_model":
                await self._call_model(cursor.turn)
            elif action == "assemble":
                answered = await self._assemble(cursor.turn)
                if answered is not None:
                    return answered
            else:
                done = await self._dispatch(cursor.turn)
                if done is not None:
                    return done

    # ---- commits ----

    async def _commit(
        self,
        kind: str,
        events: Sequence[NewEvent | CommitUnit],
        transition: Transition | None = None,
    ) -> CommitReceipt:
        """One commit, or as many consecutive ones as the journal's limit makes of ``events``;
        the transition is the last one's."""
        async with self._lock:
            riding, self._riding = self._riding, []
            parts = pack([*riding, *events], self.k.max_commit_events) or [[]]
            if len(parts) > 1:
                self.log.info(
                    "journal.commit_split",
                    kind=kind,
                    parts=len(parts),
                    events=sum(len(part) for part in parts),
                    maxEvents=self.k.max_commit_events,
                )
            receipt: CommitReceipt | None = None
            for index, part in enumerate(parts):
                last = index == len(parts) - 1
                try:
                    receipt = await self._commit_part(kind, part, transition if last else None)
                except InteractionResolved:
                    if index == 0:
                        self._riding = [*riding, *self._riding]
                    raise
            assert receipt is not None
            return receipt

    async def _commit_whole(self, kind: str, events: list[NewEvent]) -> CommitReceipt:
        """``events`` in one commit: an effect's record is journaled whole."""
        return await self._commit(kind, [CommitUnit(tuple(events))])

    async def _commit_part(
        self, kind: str, events: list[NewEvent], transition: Transition | None
    ) -> CommitReceipt:
        self.lease.check()
        self._commits += 1
        request = CommitRequest(
            command_id(self.claim.attempt_id, kind, self._commits),
            self.claim.token,
            tuple(events),
            transition,
        )
        while True:
            try:
                receipt = await self.k.journal.commit(request)
                break
            except CommitRejected as refused:
                request = await self._sealed_retry(request, refused)
            except JournalUnavailable:
                if self.lease.expired():
                    self.lease.self_fenced = True
                    self.lease.mark_lost(
                        "journal unavailable until the lease ran out"
                    )
                    raise LeaseLost(self.lease.lost_reason or "") from None
                self.log.warning(
                    "journal.commit_retry",
                    commandId=request.command_id,
                    retryS=self.k.journal_retry_s,
                )
                await self.k.sleep(self.k.journal_retry_s)
        self.log.debug(
            "journal.committed",
            commandId=request.command_id,
            events=len(request.events),
            seq=receipt.last_seq,
            transition=getattr(transition, "kind", None),
            replayed=receipt.replayed,
        )
        await afailpoint("worker.commit.after_ack")
        if receipt.events and receipt.events[0].seq == self.run.seq + 1:
            self._fold(receipt.events)
            self._at_head = True
        else:
            await self._sync()
        return receipt

    async def _sealed_retry(self, request: CommitRequest, refused: CommitRejected) -> CommitRequest:
        """The refused completion again, its child result stored sealed, once; any other refusal is raised as it
        came (CHILDREF)."""
        transition = request.transition
        sealed = (
            await sealed_completion(transition, self.content.put_sealed)
            if isinstance(transition, Complete)
            else None
        )
        if sealed is None:
            raise refused
        self.log.warning(
            "journal.commit_rejected", reason=str(refused)[:200], recovery="child_result_sealed"
        )
        self._commits += 1
        return CommitRequest(
            command_id(self.claim.attempt_id, "complete", self._commits),
            request.token,
            request.events,
            sealed,
        )

    async def _put(self, data: bytes) -> dict[str, JsonValue]:
        return await put_ref(self.content, data)

    # ---- model ----

    async def _call_model(self, turn: int) -> None:
        final = self.run.slots.get(FINAL_ANSWER_SLOT)
        if isinstance(final, int) and turn > final:
            # Legacy's ``finish_cut_short``: past the answer-only turn no working turn follows, whatever limit asked
            # for it; an answer-only turn that wrote no answer ends on the degraded one (BU-06's stall included).
            journaled = self.run.slots.get(CUT_SHORT_SLOT)
            reason: CutShort = cast("CutShort", journaled) if journaled in CUT_SHORT_REASONS else "turns"
            raise _Halt(await self._turns_exhausted(turn, reason=reason))
        # A turn the strategy took itself costs none of the model's budget.
        if turn - synthetic_turns(self.run, before=turn) > self.claim.spec.budgets.max_turns:
            if not self._takes_final_answer_turn(turn):
                raise _Halt(await self._turns_exhausted(turn, reason="turns"))
            self._final_turn, self._cut_short = turn, "turns"
        elif self._takes_final_answer_turn(turn) and self._out_of_time():
            self.log.warning("run.cut_short", reason="time", turn=turn)
            self._final_turn, self._cut_short = turn, "time"
        elif self._final_turn is None and self._takes_final_answer_turn(turn) and await self._stalled(turn):
            # Legacy's ``Agent.step`` stall check: the rounds so far made no progress, so this turn answers (BU-06).
            self.log.warning(
                "run.cut_short", reason="stall", turn=turn, staleRounds=self._stall.stale, repeats=self._stall.repeats
            )
            self._final_turn, self._cut_short = turn, "stall"
        try:
            await self._model_turn(turn)
        except _CutShort as cut:
            unpaid = cut.reason == "budget" and (self.k.wrap_up is None or not self.k.wrap_up.held)
            # A provider that refused every call through its bound would refuse the answer-only turn too.
            if self._final_turn == turn or FINAL_ANSWER_SLOT in self.run.slots or unpaid or cut.reason == "provider":
                # The answer-only turn itself could not be paid for: the answer is written without a model.
                raise _Halt(await self._turns_exhausted(turn, reason=self._cut_short or cut.reason)) from cut
            self.log.warning("run.cut_short", reason=cut.reason, turn=turn)
            self._final_turn, self._cut_short = turn, cut.reason
            try:
                await self._model_turn(turn)
            except _CutShort as again:
                raise _Halt(await self._turns_exhausted(turn, reason=cut.reason)) from again

    def _out_of_time(self) -> bool:
        """Too little of the run's working time is left for another working turn and its answer (BUDGETEND): the run
        budget's answer reserve before its deadline (Node's live one, which a person's wait moved; ADR-RUN-01 D4)."""
        return RunBudget.of_claim(self.claim).out_of_time(self.store_time.now_s())

    async def _model_turn(self, turn: int) -> None:
        await self._pre_run()
        if self._final_turn != turn:
            if self.k.wrap_up is not None:
                # Before the working phase spends: its calls then stop short of the reserve.
                _ = await self.k.wrap_up.hold()
            await self._user_compact(self.k.context or self.k.compactor)
        # No turn is left to take queued input after the budget: the answer-only turn answers what was asked.
        if self.run.queued_inputs and self.run.input_recorded and self._final_turn != turn:
            previous = self.run.response_for(turn - 1)
            boundary = "tool_results" if previous and previous.tool_calls else "turn_end"
            # Back to the loop: cancel and drain are checked before the model sees it.
            await self._consume(turn - 1, boundary)
            return
        events: list[NewEvent] = []
        if not self.run.input_recorded:
            text_ref = await self._put(self.claim.input_text.encode("utf-8"))
            events.append(
                NewEvent(
                    "message.completed",
                    {"role": "user", "textRef": text_ref, "isFinal": False},
                )
            )
        previous = self.run.turn
        if (
            previous
            and self.run.turn_completed < previous
            and self.run.response_for(previous)
        ):
            events.append(NewEvent("turn.completed", {"turn": previous}))
        if self.run.turn < turn:
            events.append(NewEvent("turn.started", {"turn": turn}))
        slots = dict(self.run.slots)
        updates = dict(self.k.turn_hook(self.run) or {}) if self.k.turn_hook is not None else {}
        plan = await self._direct(turn)
        if plan is not None:
            updates.update(plan.slots)
            events.extend(plan.events)
            if plan.cut_short is not None and self._final_turn is None:
                # The strategy ends the run on this turn, flagged (ENGBUDGET: an engine hand-off that stopped early).
                self.log.warning("run.cut_short", reason=plan.cut_short, turn=turn, by="director")
                self._final_turn, self._cut_short = turn, plan.cut_short
        changed = {
            key: value
            for key, value in updates.items()
            if slots.get(key) != value
        }
        if changed:
            slots.update(changed)
            events.append(
                NewEvent("loop.state_saved", {"slots": cast("JsonValue", changed)})
            )
        injected = plan.inject if plan is not None else ()
        if self._final_turn == turn:
            # Journaled with the turn's response: a resumed attempt that finds it takes no second one.
            # The request itself rides on the newest result's footer, per call, as legacy's (`add_loop_note`).
            reason: CutShort = self._cut_short or "turns"
            saved: dict[str, JsonValue] = {FINAL_ANSWER_SLOT: turn, CUT_SHORT_SLOT: reason}
            if reason == "budget" and self._budget_scope is not None:
                saved[BUDGET_SCOPE_SLOT] = self._budget_scope
            slots.update(saved)
            events.append(NewEvent("loop.state_saved", {"slots": cast("JsonValue", saved)}))
        for text in injected:
            events.append(
                NewEvent(
                    "message.completed",
                    {
                        "role": "user",
                        "textRef": await self._put(text.encode("utf-8")),
                        "isFinal": False,
                        "injected": True,
                    },
                )
            )
        harness = plan.harness if plan is not None else ()
        if block := harness_block(harness):
            # What the turn was told, journaled with it; as a system message, no later request renders it (D5).
            events.append(
                NewEvent(
                    "message.completed",
                    {
                        "role": HARNESS_ROLE,
                        "textRef": await self._put(block.encode("utf-8")),
                        "isFinal": False,
                        "injected": True,
                    },
                )
            )
        if plan is not None and plan.synthetic is not None:
            await self._take_turn(turn, events, plan.synthetic, injected=len(injected))
            return
        if self._policy is not None:
            # Rendered for the route the journal says the run is on: only its raw state is restored.
            await self._route(self._journaled_route(self._policy))
        schemas = self.tools.schemas()
        if self.k.delegation is not None:
            schemas = [*(schemas or []), *self.k.delegation.schemas()]
        if self.k.context is not None:
            call, sampled = await self._compiled_sample(
                self.k.context, turn, schemas, slots, events, injected, harness
            )
        else:
            call, sampled = await self._shaped_sample(turn, schemas, slots, injected, events, harness)
            # The compiler journals its own slots; a shaper's additions are journaled here.
            shaped = {key: value for key, value in call.slots.items() if slots.get(key) != value}
            if shaped:
                events.append(NewEvent("loop.state_saved", {"slots": cast("JsonValue", shaped)}))
        await afailpoint("worker.model.before_response_commit")
        if self._final_turn == turn:
            sampled = self._answer_only(sampled, turn)
        events.extend(self._provider_answered())
        journaled, presented = await self._response_events(
            sampled, call.messages, turn, digest=call.context_digest
        )
        _ = await self._commit("model", [CommitUnit((*events, *journaled)), *presented])
        self._presented.add(turn)
        self.log.info(
            "model.response",
            turn=turn,
            toolCalls=len(sampled.message.tool_calls or []),
            stopReason=str(sampled.stop_reason.value),
            provider=sampled.provider,
            model=sampled.model,
            inputTokens=sampled.usage.input_tokens,
            outputTokens=sampled.usage.output_tokens,
            reasoningTokens=sampled.usage.reasoning_tokens,
            # Per call, so a cache-placement change can be checked call by call (P4.11).
            cacheReadTokens=sampled.usage.cache_read_tokens,
            modelAttempt=self._model_attempt,
            injected=len(injected),
            **_timing_fields(sampled.timing),
        )
        await afailpoint("worker.model.after_response_commit")

    def _reducer(self) -> ContextCompiler | None:
        """What reduces history when the provider refuses a request as too large: the compiler, else the shaped
        loop's auto-compactor (QAFIX4 B2: the shaped loop no longer cuts history itself)."""
        if self.k.context is not None:
            return self.k.context
        return self.k.compactor if self.k.auto_compact else None

    async def _shaped_sample(
        self,
        turn: int,
        schemas: list[ToolSchema] | None,
        slots: dict[str, JsonValue],
        injected: Sequence[str],
        events: list[NewEvent],
        harness: Sequence[HarnessInstruction] = (),
    ) -> tuple[ModelCall, SampledTurn]:
        """The shaped request and its response; when the provider refuses it as too large, the older turns are
        compacted (journaled, the run's input kept after the summary) and the request is shaped again."""
        while True:
            if self.k.auto_compact and self.k.compactor is not None and self._final_turn != turn:
                await self._reduce(self.k.compactor, compact_only=True)
            messages, input_index = await self._shaped_history()
            if not self.run.input_recorded:
                messages.append(UserMessage(content=self.claim.input_text))
                input_index = len(messages) - 1
            messages.extend(UserMessage(content=text, injected=True) for text in injected)
            call = ModelCall(messages, schemas, self.k.system, slots, input_index)
            try:
                if self.k.shaper is not None:
                    try:
                        call = await self.k.shaper.shape(call, turn, self.run)
                    except ContextBudgetExceeded as exceeded:
                        # The host's verdict that the history no longer fits beside the prompt and tools (main
                        # #4083's budget): the provider's too-large refusal, before the request is sent.
                        raise _TooLarge from exceeded
                call = call.with_system_block(harness_block(harness))
                call = await self.tool_lists.apply(call, self.session, events)
                if self._final_turn == turn:
                    noted = list(call.messages)
                    _ = add_loop_note(noted, cut_short_note(self._cut_short or "turns"))
                    call = await self._wrap_up_call(replace(call, messages=noted, tool_choice="none"), events)
                return call, await self._sample(call)
            except _TooLarge as too_large:
                reducer = self._reducer()
                if reducer is None or not await self._compact_refused(reducer):
                    self.log.warning("model.failed", errorType=REQUEST_TOO_LARGE)
                    raise _Halt(await self._terminate(Fail(REQUEST_TOO_LARGE), "failed", "error")) from too_large

    async def _wrap_up_call(self, call: ModelCall, events: list[NewEvent]) -> ModelCall:
        """The answer-only turn after the spending limit, cut to its wrap-up reserve (BUDGETEND): the history fits
        the reserve's prompt less the system prompt and tools, and the answer its output. A cut is journaled with
        the turn's response (``WRAP_UP_FIT_SLOT``), as every cut in history is. Other limits leave the request as
        the working phase would send it."""
        wrap_up = self.k.wrap_up
        if self._cut_short != "budget" or wrap_up is None:
            return call
        system = call.system or "\n\n".join(call.system_blocks or [])
        fixed = await asyncio.to_thread(input_estimate, [], system, call.tools or [])
        history = max(wrap_up.size.input_tokens - fixed, wrap_up.size.input_tokens // 4)
        messages, input_index = fit_wrap_up(call.messages, input_index=call.input_index, history_tokens=history)
        if len(messages) != len(call.messages) or any(a is not b for a, b in zip(messages, call.messages, strict=True)):
            fit: dict[str, JsonValue] = {
                "messagesBefore": len(call.messages),
                "messagesAfter": len(messages),
                "historyTokens": history,
            }
            events.append(NewEvent("loop.state_saved", {"slots": {WRAP_UP_FIT_SLOT: fit}}))
            self.log.info("context.wrap_up_fitted", **fit)
            # Message indices moved: the compiled cache marks no longer name band edges.
            call = replace(call, messages=messages, input_index=input_index, cache_marks=None)
        return replace(call, max_output_tokens=wrap_up.size.output_tokens)

    async def _compact_refused(self, ctx: ContextCompiler) -> bool:
        """The older turns compacted because the provider refused the request as too large (P4.9), journaled with
        the chat's "Context compacted" row; ``False`` when nothing is left to compact or the summary failed."""
        items, compactions, cleared = await journal_context(self.renderer, self.session, self.claim.run_id)
        reduction = forced_compaction(items, compactions=compactions, policy=ctx.policy, window_tokens=ctx.window_tokens)
        if reduction is None:
            return False
        return await self._journal_reduction(
            ctx, reduction, items, compactions, cleared, trailer=self._auto_report(reduction, items, compactions, cleared)
        )

    def _auto_report(
        self,
        reduction: Reduction,
        items: Sequence[HistoryItem],
        compactions: Sequence[Compaction],
        cleared: Sequence[SeqSpan],
        measured: int | None = None,
    ) -> Callable[[str], list[NewEvent]]:
        """The chat's "Context compacted" row for a compaction nobody asked for (LC2FIX-3)."""

        def shown(summary: str) -> list[NewEvent]:
            if not self.claim.journal_accepts(CONTEXT_COMPACTED_AUTO):
                return []
            estimated = _context_tokens(items, compactions, cleared)
            left = _context_tokens(items, (*compactions, Compaction(reduction.span, summary)), cleared)
            before = max(estimated, measured or 0)
            report = CompactReport("compacted", before, before - (estimated - left))
            return [compacted_event("loop", report, None, trigger="auto")]

        return shown

    # ---- context compiler (P4.1, P4.2) ----

    async def _compiled_call(
        self,
        ctx: ContextCompiler,
        turn: int,
        base: ModelCall,
        events: list[NewEvent],
        injected: Sequence[str],
    ) -> ModelCall:
        """The request compiled from the journal and the host's bands, after the
        reduction (if any) the last measurement asks for is journaled."""
        if self._final_turn != turn:
            # The answer-only turn is never compacted for: it is cut to its wrap-up size instead (BUDGETEND).
            await self._reduce(ctx)
        rendered = await journal_context(self.renderer, self.session, self.claim.run_id)
        history = [message for item in rendered[0] for message in item.messages]
        bands = (
            await ctx.host.bands(replace(base, messages=history), turn, self.run)
            if ctx.host is not None
            else default_bands(base)
        )
        last = history[-1] if history else None
        final_note = cut_short_note(self._cut_short or "turns")
        if self._final_turn == turn and isinstance(last, ToolMessage):
            # Legacy's answer-only request on the newest result's footer; no later request renders it again.
            footer = bands.result_footers.get(last.tool_call_id, "") + "\n" + final_note
            bands = replace(bands, result_footers={**bands.result_footers, last.tool_call_id: footer})
        elif self._final_turn == turn:
            injected = (*injected, final_note)
        # The host's own slots go out in the compiler's one `loop.state_saved`; the shaped path's diff never sees
        # a compiled call, so nothing is journaled twice.
        saved: dict[str, JsonValue] = {
            key: value for key, value in bands.slots.items() if base.slots.get(key) != value
        }
        bands = self._journaled_footers(bands, saved)
        if bands.window_tokens is None:
            # A batch's results share a slice of the window (BUDGETEND).
            bands = replace(bands, window_tokens=ctx.window_tokens)
        cap = base.slots.get(RESULT_CAP_SLOT)
        if isinstance(cap, int):
            bands = replace(bands, max_result_chars=min(cap, bands.max_result_chars or cap))
        self._result_cap = bands.max_result_chars
        notes = tuple(text for text in bands.notes if text)
        bands = replace(bands, notes=())
        journaled_texts = journaled_notes(base.slots.get(NOTES_SLOT))
        journaled = base.slots.get(TOOL_ORDER_SLOT)
        order = [str(t) for t in journaled] if isinstance(journaled, list) else []

        async def compile_with(fresh: Sequence[str]) -> CompiledContext:
            note = note_message(fresh)
            pending: list[Message] = []
            if not self.run.input_recorded:
                pending.append(UserMessage(content=self.claim.input_text))
                # After the input: read before it, the note halved react-04's pass rate (P4.13-P4.15).
                pending.extend([UserMessage(content=note, injected=True)] if note else [])
            pending.extend(UserMessage(content=text, injected=True) for text in injected)
            if note and self.run.input_recorded:
                pending.append(UserMessage(content=note, injected=True))
            return await compile_session(
                self.renderer,
                self.session,
                run_id=self.claim.run_id,
                bands=bands,
                tool_order=order,
                pending=pending,
                pending_input=None if self.run.input_recorded else 0,
                rendered=rendered,
            )

        fresh = new_notes(notes, journaled_texts)
        compiled = await compile_with(fresh)
        lost = {text for text in notes if text not in fresh and not held(compiled.history, text)}
        if lost:
            # A compaction summarised them away: journaled again, after the summary.
            fresh = tuple(text for text in notes if text in lost or text in fresh)
            compiled = await compile_with(fresh)
        if fresh:
            note_event = await self._note_event(note_message(fresh))
            if self.run.input_recorded:
                events.append(note_event)
            else:
                at = next(i for i, e in enumerate(events) if e.type == "message.completed")
                events.insert(at + 1, note_event)
            saved[NOTES_SLOT] = list(dict.fromkeys([*journaled_texts, *fresh]))
        slots = dict(base.slots)
        if list(compiled.tool_order) != order:
            saved[TOOL_ORDER_SLOT] = list(compiled.tool_order)
        if saved:
            slots.update(saved)
            events.append(NewEvent("loop.state_saved", {"slots": saved}))
        self.log.debug(
            "context.compiled",
            turn=turn,
            messages=len(compiled.history) + len(compiled.tail),
            tools=len(compiled.tools),
            digest=compiled.digest(),
        )
        return ModelCall(
            compiled.messages,
            list(compiled.tools) or None,
            compiled.system_text,
            slots,
            compiled.input_index,
            system_blocks=compiled.system_blocks or None,
            cache_marks=compiled.cache_marks,
            context_digest=compiled.digest(),
            tool_list_key=bands.tool_list_key,
        )

    async def _compiled_sample(
        self,
        ctx: ContextCompiler,
        turn: int,
        schemas: list[ToolSchema] | None,
        slots: dict[str, JsonValue],
        events: list[NewEvent],
        injected: Sequence[str],
        harness: Sequence[HarnessInstruction] = (),
    ) -> tuple[ModelCall, SampledTurn]:
        """Compile and sample; when the provider refuses the request as too large, journal a reduction
        (the older turns compacted, else a smaller per-result cap) and compile again from the journal."""
        pending = list(events)
        while True:
            events[:] = pending
            call = await self._compiled_call(ctx, turn, ModelCall([], schemas, self.k.system, slots), events, injected)
            call = call.with_system_block(harness_block(harness))
            call = await self.tool_lists.apply(call, self.session, events)
            if self._final_turn == turn:
                call = await self._wrap_up_call(replace(call, tool_choice="none"), events)
            try:
                return call, await self._sample(call)
            except _TooLarge as too_large:
                cap = await self._shrink(ctx, call)
                if cap is None:
                    self.log.warning("model.failed", errorType=REQUEST_TOO_LARGE)
                    raise _Halt(await self._terminate(Fail(REQUEST_TOO_LARGE), "failed", "error")) from too_large
                if cap:
                    slots[RESULT_CAP_SLOT] = cap

    async def _shrink(self, ctx: ContextCompiler, call: ModelCall) -> int | None:
        """One reduction the provider's refusal asks for, journaled before the request is sent again: a
        compaction of the older turns (0), else half the per-result cap in force (the new cap), else
        ``None`` (nothing left to reduce: the request is refused, never sent oversized)."""
        if await self._compact_refused(ctx):
            return 0
        in_force = call.slots.get(RESULT_CAP_SLOT)
        cap = (in_force if isinstance(in_force, int) else self._result_cap or RESULT_CAP_CHARS) // 2
        if cap < RESULT_CAP_FLOOR:
            return None
        _ = await self._commit("context", [NewEvent("loop.state_saved", {"slots": {RESULT_CAP_SLOT: cap}})])
        self.log.warning("context.result_capped", reason=REQUEST_TOO_LARGE, maxResultChars=cap)
        await afailpoint("worker.context.after_cap_commit")
        return cap

    async def _note_event(self, text: str) -> NewEvent:
        return NewEvent(
            "message.completed",
            {"role": "user", "textRef": await self._put(text.encode("utf-8")), "isFinal": False, "injected": True},
        )

    def _journaled_footers(self, bands: HostBands, saved: dict[str, JsonValue]) -> HostBands:
        """Every run's journaled footers with the host's for this run; the host's new ones are saved with
        this commit, so a later request renders each result with the bytes it was first sent with (a footer
        the host gives once, the deadline warning on the newest result, stays)."""
        journaled = self.session.result_footers
        fresh = {call: footer for call, footer in bands.result_footers.items() if call not in journaled}
        if fresh:
            saved[RESULT_FOOTERS_SLOT] = cast("JsonValue", fresh)
        if not journaled:
            return bands
        return replace(bands, result_footers={**bands.result_footers, **journaled})

    async def _shaped_history(self) -> tuple[list[Message], int | None]:
        """The transcript the shaper starts from; with a journaled compaction (``/compact``), the history it left."""
        if not self.session.compactions:
            return await self.renderer.render_for(self.session, self.claim.run_id)
        items, compactions, _ = await journal_context(self.renderer, self.session, self.claim.run_id)
        return compacted_history(items, compactions)

    async def _user_compact(self, ctx: ContextCompiler | None) -> None:
        """The person's ``/compact [focus]`` (SLASH2 C2), once per run, before its first request: everything before
        the last response summarised, journaled as the ladder's compaction is, with ``x.pipeshub.context.compacted``
        and the slot that says it was done in the same commit. This run's own input follows the summary."""
        ask = self.claim.compact
        if ask is None or USER_COMPACT_SLOT in self.run.slots:
            return
        items, compactions, cleared = await journal_context(self.renderer, self.session, self.claim.run_id)
        estimated = _context_tokens(items, compactions, cleared)
        # The provider's count of the latest request (system and tools too) when it says more than the estimate.
        measured = latest_measurement(self.session)
        before = max(estimated, measured.tokens if measured is not None else 0)
        reduction = (
            forced_compaction(
                items, compactions=compactions, policy=replace(ctx.policy, keep_recent_turns=USER_COMPACT_KEEP_TURNS)
            )
            if ctx is not None
            else None
        )

        def recorded(report: CompactReport) -> list[NewEvent]:
            events = [NewEvent("loop.state_saved", {"slots": {USER_COMPACT_SLOT: report.outcome}})]
            if self.claim.journal_accepts(CONTEXT_COMPACTED):
                events.insert(0, compacted_event("loop", report, ask.focus))
            self.log.info(
                "context.user_compacted", outcome=report.outcome, beforeTokens=report.before_tokens,
                afterTokens=report.after_tokens, focus=ask.focus is not None,
            )
            return events

        def compacted(summary: str) -> list[NewEvent]:
            assert reduction is not None
            left = _context_tokens(items, (*compactions, Compaction(reduction.span, summary)), cleared)
            return recorded(CompactReport("compacted", before, before - (estimated - left)))

        if ctx is not None and reduction is not None and await self._journal_reduction(
            ctx, reduction, items, compactions, cleared, focus=ask.focus, trailer=compacted
        ):
            return
        outcome = "unsupported" if ctx is None else "nothing_to_compact" if reduction is None else "failed"
        _ = await self._commit("context", recorded(CompactReport(outcome, before, before)))

    async def _reduce(self, ctx: ContextCompiler, *, compact_only: bool = False) -> None:
        """One rung of the ladder when the provider's last count asks for it, journaled
        before the request that uses it (REPORT 6.3); ``compact_only``: never the clearing rung."""
        items, compactions, cleared = await journal_context(self.renderer, self.session, self.claim.run_id)
        measurement = latest_measurement(self.session)
        reduction = plan_reduction(
            items,
            measurement=measurement,
            window_tokens=ctx.window_tokens,
            compactions=compactions,
            cleared=cleared,
            reduced_at_seq=self.session.reduced_at_seq,
            policy=replace(ctx.policy, clear_above=ctx.policy.compact_above) if compact_only else ctx.policy,
        )
        if reduction is None or measurement is None:
            return
        shown = self._auto_report(reduction, items, compactions, cleared, measurement.tokens)
        _ = await self._journal_reduction(
            ctx, reduction, items, compactions, cleared, measured=measurement.tokens, trailer=shown
        )

    async def _journal_reduction(
        self,
        ctx: ContextCompiler,
        reduction: Reduction,
        items: Sequence[HistoryItem],
        compactions: Sequence[Compaction],
        cleared: Sequence[SeqSpan],
        *,
        measured: int | None = None,
        focus: str | None = None,
        trailer: Callable[[str], list[NewEvent]] | None = None,
    ) -> bool:
        """``reduction`` journaled with the join of its range's labels; ``False`` when the strategy could
        not summarise the range. ``trailer``: events journaled in the same commit, from the summary."""
        span = reduction.span
        labels = range_labels(await self._read(after_seq=span.from_seq - 1), span.from_seq, span.to_seq)
        data: dict[str, JsonValue] = {"range": {"fromSeq": span.from_seq, "toSeq": span.to_seq}}
        event_type = "context.cleared"
        if reduction.kind == "compact":
            covered = compile_context(
                CompilerInput(
                    host=HostBands(),
                    history=tuple(i for i in items if span.covers(i)),
                    compactions=tuple(compactions),
                    cleared=tuple(cleared),
                )
            )
            summary = await self._compact(
                ctx,
                CompactionRequest(
                    span, covered.history, labels, ctx.summary_chars, focus=focus, input_chars=ctx.summary_input_chars
                ),
            )
            if summary is None:
                return False
            await afailpoint("worker.compaction.before_event")
            data["summaryRef"] = await self._put(summary.text.encode("utf-8"))
            event_type = "context.compacted"
        trailing = trailer(summary.text) if trailer is not None and reduction.kind == "compact" else []
        _ = await self._commit("context", [NewEvent(event_type, data, labels=labels_json(labels)), *trailing])
        self.log.info(
            event_type,
            fromSeq=span.from_seq,
            toSeq=span.to_seq,
            measuredTokens=measured,
            windowTokens=ctx.window_tokens,
            integrity=labels.integrity if labels is not None else None,
        )
        return True

    async def _compact(
        self, ctx: ContextCompiler, request: CompactionRequest
    ) -> CompactionSummary | None:
        """The strategy's summary; a budget refusal ends the run (refuse, never downgrade),
        an unreachable ledger yields it, and a strategy that cannot summarise leaves the
        context unreduced for this request."""
        try:
            return await ctx.strategy.compact(request)
        except BudgetUnavailable as exc:
            self.log.warning("context.compaction_refused", reason=exc.reason_code, choice="yield")
            raise _Halt(await self._yield("llm_backoff", ctx.ledger_backoff_s)) from exc
        except BudgetRefused as exc:
            if exc.reason_code in BUDGET_STOPS:
                self._budget_scope = self._budget_scope or exc.budget_scope
                # No spend is left to compact: the run answers from what it has (BUDGETEND).
                self.log.warning("context.compaction_refused", reason=exc.reason_code, choice="wrap_up")
                raise _CutShort("budget") from exc
            self.log.warning("context.compaction_refused", reason=exc.reason_code, choice="fail")
            raise _Halt(
                await self._terminate(Fail(_error_type(exc.reason_code)), "failed", "error")
            ) from exc
        except CompactionRefused as exc:
            self.log.warning("context.compaction_refused", reason=exc.reason_code, choice="skip")
            return None
        except Exception as exc:
            self.log.warning(
                "context.compaction_refused", reason="compaction_failed", errorType=type(exc).__name__, choice="skip"
            )
            return None

    async def _direct(self, turn: int) -> TurnPlan | None:
        """The loop strategy's plan for ``turn``, from the journal alone, under the run's plan gate (G-04): a declined
        plan ends the run with the gate's own answer, and the first turn of a gated run is told to plan first."""
        gate = self.plan_gate
        if gate is not None and gate.declined:
            self.log.info("gate.plan_declined", turn=turn, rule=PLAN_RULE)
            return TurnPlan(synthetic=AssistantMessage(content=[TextPart(text=PLAN_DECLINED_ANSWER)]))
        director = self.k.director
        if director is None:
            plan = TurnPlan()
        else:
            previous = await self._turn_results(turn - 1)
            plan = director.before(turn, self.run, previous)
        if not self.run.responses and (notes := gate_notes(self.claim.spec)):
            plan = replace(plan, inject=(*notes, *plan.inject))
        if director is None and not plan.inject:
            return None
        if plan.inject or plan.harness or plan.synthetic is not None:
            self.log.info(
                "loop.directed",
                turn=turn,
                injected=len(plan.inject),
                harness=[i.key for i in plan.harness],
                synthetic=plan.synthetic is not None,
                slots=sorted(plan.slots),
            )
        return plan

    async def _stalled(self, turn: int) -> bool:
        """Whether the tool rounds before ``turn`` made no progress, counted as legacy counts its step footers."""
        for earlier in range(self._stall_turn + 1, turn):
            invocations = self.run.invocations_for(earlier)
            response = self.run.response_for(earlier)
            self._stall_turn = earlier
            if not invocations or any(i.state != "completed" for i in invocations):
                continue
            if response is not None and is_synthetic(response):
                continue
            calls: list[RoundCall] = []
            for inv in invocations:
                content: JsonValue = inv.status
                is_error = inv.status != "ok"
                if inv.result_ref is not None:
                    content, failed = decode_result(await self.renderer.blob(inv.result_ref))
                    is_error = is_error or failed
                calls.append(RoundCall(inv.name, inv.args_digest, is_error, content))
            self._stall = next_stall(self._stall, calls)
        return stalled(self._stall)

    async def _turn_results(self, turn: int) -> list[TurnResult]:
        """``turn``'s journaled results in ``toolCalls`` order, content decoded."""
        results: list[TurnResult] = []
        if turn < 1:
            return results
        for inv in self.run.invocations_for(turn):
            if inv.state != "completed":
                continue
            content: JsonValue = inv.status
            if inv.result_ref is not None:
                content, _ = decode_result(await self.renderer.blob(inv.result_ref))
            results.append(
                TurnResult(
                    name=inv.name,
                    call_id=inv.call_id,
                    status=inv.status or "error",
                    content=content,
                )
            )
        return results

    async def _take_turn(
        self,
        turn: int,
        events: list[NewEvent],
        message: AssistantMessage,
        *,
        injected: int,
    ) -> None:
        """A turn the strategy takes itself: journaled as its response, no model call."""
        sampled = SampledTurn(
            message=message,
            provider=SYNTHETIC_PROVIDER,
            transport=SYNTHETIC_TRANSPORT,
            model=SYNTHETIC_PROVIDER,
        )
        await afailpoint("worker.model.before_response_commit")
        journaled, presented = await self._response_events(sampled, [], turn, synthetic=True)
        _ = await self._commit("model", [CommitUnit((*events, *journaled)), *presented])
        self._presented.add(turn)
        self.log.info(
            "model.synthetic",
            turn=turn,
            toolCalls=len(message.tool_calls or []),
            injected=injected,
        )
        await afailpoint("worker.model.after_response_commit")

    async def _pre_run(self) -> None:
        if self.run.responses:
            return
        missing = [
            s
            for s in self.k.pre_run
            if PRE_RUN_SLOT_PREFIX + s.key not in self.run.slots
        ]
        if missing:
            await self._run_pre_run(missing)
        gate = self.k.pre_run_gate
        if gate is not None and gate.needs_input(self.run.slots):
            # Node handed this run the person's /compact and cleared it: done before the question ends the run,
            # or it is lost with no row saying so (LONGTASK-B3).
            await self._user_compact(self.k.context or self.k.compactor)
            raise _Halt(await self._ask_before_model())

    async def _ask_before_model(self) -> Disposition:
        """The pre-run decided to ask the user first: no model call."""
        events: list[NewEvent] = []
        if not self.run.input_recorded:
            text_ref = await self._put(self.claim.input_text.encode("utf-8"))
            events.append(
                NewEvent(
                    "message.completed",
                    {"role": "user", "textRef": text_ref, "isFinal": False},
                )
            )
        events.extend(await self._finalize("needs_input"))
        # Asked before the agent ran: input queued meanwhile is left for the user to resend.
        _ = await self._commit(
            "complete",
            events,
            await self._as_child(Complete("needs_input", leaves_input=True)),
        )
        self.log.info(
            "run.complete",
            outcome="needs_input",
            reason="pre_run",
            leftInputs=len(self.run.queued_inputs),
        )
        return Disposition("completed", "needs_input")

    async def _run_pre_run(self, missing: list[PreRunStep]) -> None:
        slots: dict[str, JsonValue] = {}
        for step in missing:
            slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(
                self.claim.input_text
            )
        events = [NewEvent("loop.state_saved", {"slots": slots})]
        events.extend(self._present_pre_run({**self.run.slots, **slots}))
        _ = await self._commit("prerun", events)
        self.log.info("run.pre_run", steps=sorted(step.key for step in missing))
        await afailpoint("worker.prerun.after_commit")

    async def _sample(self, call: ModelCall) -> SampledTurn:
        if self._policy is not None:
            return await self._sample_routed(call, self._policy)
        waited = 0
        failures = 0
        while True:
            self.lease.check()
            self._model_attempt += 1
            self._streamed = []
            if self._final_turn is not None and self.k.wrap_up is not None:
                # The answer-only turn draws on the reserve held for it, never on a new reservation.
                _ = self.k.wrap_up.arm()
            live = _LiveStream(self, self._model_attempt)
            try:
                try:
                    return await self._traced_sample(self.k.model, call, live)
                finally:
                    await live.flush()
            except ModelBackoff as backoff:
                if (
                    backoff.retry_after_s <= self.k.inline_backoff_s
                    and waited < MAX_EFFECT_RETRIES
                ):
                    waited += 1
                    self.log.warning(
                        "model.backoff",
                        retryAfterS=backoff.retry_after_s,
                        waited=waited,
                        choice="wait",
                    )
                    await self.k.sleep(backoff.retry_after_s)
                    continue
                await self._backoff(backoff)
            except ModelFailed as failed:
                if failed.error_type in BUDGET_STOPS:
                    self._budget_scope = self._budget_scope or failed.budget_scope
                    self.log.warning("model.budget_stop", errorType=failed.error_type)
                    raise _CutShort("budget") from failed
                if failed.error_type == REQUEST_TOO_LARGE and self._reducer() is not None:
                    raise _TooLarge from failed
                # Without a policy there is one route: a transient failure is retried on it (P4.9).
                if await self._retried(failed, failures, ModelPolicyConfig(), None):
                    failures += 1
                    continue
                raise _Halt(await self._model_failed(failed)) from failed

    async def _backoff(self, backoff: ModelBackoff) -> NoReturn:
        """The model's call must wait longer than an attempt holds its slot: the run yields until then (P4.9). A
        provider's waits are bounded (``provider_wait``, ADR-RUN-01 FIX-429): counted in a slot that survives
        re-claims, and past the bound the run ends cut short by the provider with what it gathered. The ledger's
        own outage is not the provider's, so its waits are not counted."""
        if not backoff.provider:
            self.log.warning("model.backoff", retryAfterS=backoff.retry_after_s, choice="yield")
            raise _Halt(await self._yield("llm_backoff", backoff.retry_after_s)) from backoff
        waits = ProviderWaits.of_slot(self.run.slots.get(PROVIDER_WAIT_SLOT))
        delay = waits.next_delay(backoff.retry_after_s)
        if delay is None:
            self.log.warning(
                "model.backoff",
                retryAfterS=backoff.retry_after_s,
                waits=waits.count,
                waitedS=round(waits.waited_s, 3),
                choice="cut_short",
            )
            raise _CutShort("provider") from backoff
        self.log.warning(
            "model.backoff", retryAfterS=backoff.retry_after_s, wait=waits.count + 1, delayS=delay, choice="yield"
        )
        counted = NewEvent("loop.state_saved", {"slots": {PROVIDER_WAIT_SLOT: waits.after(delay).slot()}})
        raise _Halt(await self._yield("llm_backoff", delay, [counted])) from backoff

    def _provider_answered(self) -> list[NewEvent]:
        """A call the provider answered ends its outage: the next one gets the whole bound again."""
        if ProviderWaits.of_slot(self.run.slots.get(PROVIDER_WAIT_SLOT)).count == 0:
            return []
        return [NewEvent("loop.state_saved", {"slots": {PROVIDER_WAIT_SLOT: ProviderWaits().slot()}})]

    async def _retried(
        self, failed: ModelFailed, failures: int, config: ModelPolicyConfig, route: PolicyRoute | None
    ) -> bool:
        """Waits out the delay before retrying ``failed`` on the same route as a new model attempt, and
        says whether to: only a transient failure (P4.7 ``RETRIED``), at most ``max_retries`` in a row, and
        never past this attempt's lease, which Node bounds by the run's deadline. A budget refusal is not
        transient, so a retry is never paid for beyond what the ledger grants."""
        trigger = classify_failure(failed)
        if trigger not in RETRIED or failures >= config.max_retries:
            return False
        delay = config.delay_s(failures + 1)
        if delay >= self.lease.remaining():
            self.log.warning("model.retry_skipped", reason=trigger, failures=failures + 1, delayS=delay)
            return False
        self.log.warning(
            "model.retry",
            reason=trigger,
            failures=failures + 1,
            delayS=delay,
            **({"route": route.route_id} if route is not None else {}),
        )
        await self.k.sleep(delay)
        return True

    # ---- model policy (P4.7) ----

    def _journaled_route(self, policy: ModelPolicy) -> PolicyRoute:
        moved = self.run.model_route
        return policy.primary if moved is None else policy.routed_to(moved.provider, moved.model)

    async def _route(self, route: PolicyRoute) -> None:
        routed = cast("RoutedModelPort", self.k.model)
        if routed.active == route:
            return
        try:
            await routed.select(route)
        except Exception as exc:
            # The journal moved the run to a route this worker cannot reach: it is not run elsewhere.
            self.log.warning("model.route_unavailable", route=route.route_id, errorType=type(exc).__name__)
            raise _Halt(await self._terminate(Fail(MODEL_ROUTE_UNAVAILABLE), "failed", "error")) from exc

    @property
    def _run_ref(self) -> RunRef:
        c = self.claim
        return RunRef(c.org_id, c.run_id, c.session_id, c.attempt_id, c.fence)

    async def _traced_sample(self, model: DurableModelPort, call: ModelCall, live: _LiveStream) -> SampledTurn:
        """One model attempt as a GenAI ``chat`` span (OB-01): the loop's calls reach the provider from this process,
        not through the Model Gateway, so the span is the kernel's."""
        span = self.k.tracer.start(
            genai.CHAT,
            kind="client",
            run=self._run_ref,
            attributes={genai.OPERATION: genai.CHAT, "pipeshub.model_attempt": self._model_attempt},
        )
        try:
            sampled = await self._unless_cancelled(model.sample(call, live.publish), streams=True)
        except BaseException as exc:
            span.end(error_type=getattr(exc, "error_type", None) or type(exc).__name__)
            raise
        span.rename(f"{genai.CHAT} {sampled.model}")
        span.set(
            {
                genai.PROVIDER: genai.provider_name(sampled.provider),
                genai.RESPONSE_MODEL: sampled.model,
                **genai.usage_attributes(sampled.usage),
            }
        )
        span.end()
        return sampled

    def _tool_span(self, p: _Planned) -> SpanHandle:
        return self.k.tracer.start(
            f"{genai.EXECUTE_TOOL} {p.call.name}",
            kind="internal",
            run=self._run_ref,
            attributes={
                genai.OPERATION: genai.EXECUTE_TOOL,
                genai.TOOL_NAME: p.call.name,
                genai.TOOL_TYPE: "function",
                genai.TOOL_CALL_ID: p.inv.invocation_id,
                "pipeshub.tool_id": p.effect.tool_id,
            },
        )

    async def _sample_routed(self, call: ModelCall, policy: ModelPolicy) -> SampledTurn:
        """``_sample`` with the run's policy: a transient failure is retried on the route as a new
        model attempt (its deltas never mix with the failed attempt's), and a trigger the next
        pinned route takes moves the run there, journaled before that route is called."""
        routed = cast("RoutedModelPort", self.k.model)
        route = routed.active
        waited = 0
        failures = 0
        while True:
            self.lease.check()
            self._model_attempt += 1
            self._streamed = []
            if self._final_turn is not None and self.k.wrap_up is not None:
                # The answer-only turn draws on the reserve held for it, never on a new reservation.
                _ = self.k.wrap_up.arm()
            live = _LiveStream(self, self._model_attempt)
            try:
                try:
                    return await self._traced_sample(routed, call, live)
                finally:
                    await live.flush()
            except ModelBackoff as backoff:
                if backoff.retry_after_s <= self.k.inline_backoff_s and waited < MAX_EFFECT_RETRIES:
                    waited += 1
                    self.log.warning("model.backoff", retryAfterS=backoff.retry_after_s, waited=waited, choice="wait")
                    await self.k.sleep(backoff.retry_after_s)
                    continue
                moved = await self._fall_back(policy, route, "429_exhausted")
                if moved is None:
                    await self._backoff(backoff)
            except ModelFailed as failed:
                if failed.error_type in BUDGET_STOPS:
                    self._budget_scope = self._budget_scope or failed.budget_scope
                    self.log.warning("model.budget_stop", errorType=failed.error_type)
                    raise _CutShort("budget") from failed
                if failed.error_type == REQUEST_TOO_LARGE and self._reducer() is not None:
                    raise _TooLarge from failed
                if await self._retried(failed, failures, policy.config, route):
                    failures += 1
                    continue
                trigger = classify_failure(failed)
                moved = await self._fall_back(policy, route, trigger) if trigger is not None else None
                if moved is None:
                    raise _Halt(await self._model_failed(failed)) from failed
            route, waited, failures = moved, 0, 0

    async def _fall_back(
        self, policy: ModelPolicy, current: PolicyRoute, trigger: FallbackTrigger
    ) -> PolicyRoute | None:
        """Move to the next pinned route that takes ``trigger``, journaled first; ``None`` when none can."""
        routed = cast("RoutedModelPort", self.k.model)
        candidate = policy.next_route(current, trigger)
        while candidate is not None:
            try:
                await routed.select(candidate)
            except Exception as exc:
                self.log.warning("model.route_unavailable", route=candidate.route_id, errorType=type(exc).__name__)
                candidate = policy.next_route(candidate, trigger)
                continue
            data: dict[str, JsonValue] = {
                "from": {"provider": current.provider, "model": current.model},
                "to": {"provider": candidate.provider, "model": candidate.model},
                "reason": trigger,
            }
            _ = await self._commit("fallback", [NewEvent("model.fallback", data, critical=False)])
            self.log.warning(
                "model.fallback", reason=trigger, fromRoute=current.route_id, toRoute=candidate.route_id
            )
            await afailpoint("worker.model.after_fallback_commit")
            return candidate
        return None

    def streamed(self, text: str) -> None:
        self._streamed.append(text)

    async def publish_live(self, frame: LiveFrame) -> None:
        """Best effort by design: the live bus is never on the execution path."""
        live = self.k.live
        if live is None:
            return
        try:
            await live.publish(frame)
        except Exception as exc:
            if not self._live_failed:
                self._live_failed = True
                self.log.warning(
                    "live.degraded",
                    reason="publish_failed",
                    errorType=type(exc).__name__,
                    modelAttempt=frame.model_attempt,
                )

    async def _response_events(
        self,
        sampled: SampledTurn,
        messages: list[Message],
        turn: int,
        *,
        synthetic: bool = False,
        digest: str | None = None,
    ) -> tuple[list[NewEvent], list[NewEvent]]:
        """The response as journaled, and how the host presents it: the first commits whole, with what the
        turn journals before it; the second follows it when one commit cannot hold both."""
        message = sampled.message
        calls = [
            call if call.id else call.model_copy(update={"id": f"call_{turn}_{i}"})
            for i, call in enumerate(message.tool_calls or [])
        ]
        message = message.model_copy(update={"tool_calls": calls or None})
        # A synthetic turn's text is the strategy's, not the model's: only its calls are shown.
        presented = self._present(
            message.model_copy(update={"content": ""}) if synthetic else message
        )
        response: dict[str, JsonValue] = {
            "messageRef": await self._put(encode_assistant(message)),
            "toolCalls": [
                {
                    "callId": c.id,
                    "modelName": _model_tool_name(c.name),
                    "argsDigest": canonical_digest(cast("JsonValue", c.arguments)),
                }
                for c in calls
            ],
            "usage": _journal_usage(sampled.usage),
            "provider": _machine_name(sampled.provider),
            "transport": _machine_name(sampled.transport),
        }
        if sampled.raw is not None:
            response["rawRef"] = await self._put(sampled.raw)
        if synthetic:
            return [NewEvent("model.response", response)], presented
        context = digest or canonical_digest([m.model_dump(mode="json") for m in messages])
        request: dict[str, JsonValue] = {
            "contextDigest": context,
            "route": {
                "provider": _machine_name(sampled.provider),
                "model": sampled.model or "unknown",
            },
            "modelAttempt": self._model_attempt,
        }
        return [NewEvent("model.request", request), NewEvent("model.response", response)], presented

    async def _present_rest(self, turn: int, message: AssistantMessage, *, synthetic: bool) -> None:
        """A response an earlier attempt journaled with more presentation than its commit held: the
        attempt may have died between the two commits. What the journal lacks of it is written now, so
        every call of the turn is presented once."""
        if self.k.presenter is None or turn in self._presented:
            return
        self._presented.add(turn)
        presented = self._present(message.model_copy(update={"content": ""}) if synthetic else message)
        if len(presented) <= self.k.max_commit_events - HEAD_RESERVE:
            return
        have = [(e.type, e.data) for e in await self._this_run_events()]
        missing = [e for e in presented if (e.type, e.data) not in have]
        if missing:
            self.log.info("presentation.completed", turn=turn, missing=len(missing), presented=len(presented))
            _ = await self._commit("present", missing)

    def _present_pre_run(self, slots: dict[str, JsonValue]) -> list[NewEvent]:
        if self.k.presenter is None:
            return []
        try:
            return self.k.presenter.pre_run(slots)
        except Exception as exc:
            self.log.warning(
                "presentation.failed", stage="pre_run", errorType=type(exc).__name__
            )
            return []

    def _present(self, message: AssistantMessage) -> list[NewEvent]:
        if self.k.presenter is None:
            return []
        try:
            return self.k.presenter.response(message)
        except Exception as exc:
            self.log.warning(
                "presentation.failed", stage="response", errorType=type(exc).__name__
            )
            return []

    def _journaled_budget_scope(self) -> str | None:
        """Which shared budget cut the run short: this attempt's refusal, else the one journaled with the cut."""
        if self._budget_scope is not None:
            return self._budget_scope
        journaled = self.run.slots.get(BUDGET_SCOPE_SLOT)
        return journaled if isinstance(journaled, str) else None

    async def _finalize(
        self,
        outcome: RunEndingOutcome,
        error_type: str | None = None,
        answer: str | None = None,
        confidence: str | None = None,
        truncated_by: CutShort | None = None,
    ) -> list[NewEvent]:
        if self.k.presenter is None and not self.k.post_run and not self.k.after_run:
            return []
        # The presenter reads only this worker's facts, all folded already.
        events = await self._this_run_events()
        try:
            texts, tool_calls = await self._assistant_turns()
        except Exception as exc:
            self.log.warning(
                "presentation.failed", stage="finalize", errorType=type(exc).__name__
            )
            return []
        ending = RunEnding(
            outcome,
            texts,
            error_type,
            events,
            answer,
            confidence,
            tool_calls,
            truncated_by,
            self._journaled_budget_scope() if truncated_by == "budget" else None,
        )
        settled = await self._post_run(ending)
        await self._prepare_after(ending)
        if self.k.presenter is None:
            return settled
        try:
            return [*settled, *(await self.k.presenter.finalize(ending))]
        except Exception as exc:
            self.log.warning(
                "presentation.failed", stage="finalize", errorType=type(exc).__name__
            )
            return settled

    async def _prepare_after(self, ending: RunEnding) -> None:
        for step in self.k.after_run:
            try:
                prepared = await step.prepare(ending)
            except Exception as exc:
                self.log.warning("after_run.unprepared", step=step.key, errorType=type(exc).__name__)
                continue
            if prepared is not None:
                self._after.append((step, prepared))

    async def _after_committed(self, landed: bool) -> None:
        """Starts the prepared after-run steps once the terminal commit landed; the run's end never waits for them."""
        after, self._after = self._after, []
        for step, prepared in after:
            if landed:
                self.k.spawn(self._after_run(step, prepared))
                continue
            try:
                await step.discard(prepared)
            except Exception as exc:
                self.log.warning("after_run.discard_failed", step=step.key, errorType=type(exc).__name__)

    async def _after_run(self, step: AfterRunStep, prepared: object) -> None:
        try:
            await step.run(prepared)
        except Exception as exc:
            self.log.warning("after_run.failed", step=step.key, errorType=type(exc).__name__)
        else:
            self.log.info("after_run.done", step=step.key)

    async def _post_run(self, ending: RunEnding) -> list[NewEvent]:
        """The post-run steps no attempt has started, at most once each."""
        pending: list[PostRunStep] = []
        for step in self.k.post_run:
            slot = self.run.slots.get(POST_RUN_SLOT_PREFIX + step.key)
            if slot is None:
                pending.append(step)
            elif isinstance(slot, dict) and slot.get("state") == "started":
                # Started by an attempt that never committed the run's ending: its
                # unjournaled writes may or may not have happened.
                self.log.warning("post_run.unknown", step=step.key, outcome=ending.outcome)
        if not pending:
            return []
        started: dict[str, JsonValue] = {
            POST_RUN_SLOT_PREFIX + step.key: {"state": "started"} for step in pending
        }
        _ = await self._commit(
            "postrun", [NewEvent("loop.state_saved", {"slots": started})]
        )
        await afailpoint("worker.postrun.after_commit")
        settled: dict[str, JsonValue] = {}
        for step in pending:
            slot_key = POST_RUN_SLOT_PREFIX + step.key
            try:
                result = await step.run(ending)
            except Exception as exc:
                self.log.warning(
                    "post_run.failed", step=step.key, errorType=type(exc).__name__
                )
                settled[slot_key] = {"state": "failed", "errorType": type(exc).__name__}
                continue
            self.log.info("post_run.done", step=step.key, outcome=ending.outcome)
            settled[slot_key] = {"state": "done", "result": result}
        return [NewEvent("loop.state_saved", {"slots": settled})]

    async def _assistant_texts(self) -> tuple[str, ...]:
        return (await self._assistant_turns())[0]

    async def _assistant_turns(self) -> tuple[tuple[str, ...], tuple[ToolCall, ...]]:
        texts: list[str] = []
        calls: list[ToolCall] = []
        for entry in self.session.entries:
            if entry.run_id != self.claim.run_id or entry.response is None:
                continue
            if is_synthetic(entry.response):
                continue
            message = decode_assistant(
                await self.renderer.blob(entry.response.message_ref)
            )
            texts.append(message.text)
            calls.extend(message.tool_calls or ())
        return tuple(texts), tuple(calls)

    async def _assemble(self, turn: int) -> Disposition | None:
        response = self.run.response_for(turn)
        assert response is not None
        message = decode_assistant(await self.renderer.blob(response.message_ref))
        final_turn = self.run.slots.get(FINAL_ANSWER_SLOT) == turn
        # The answer-only turn has no turn after it to continue a cut answer in.
        if self.k.review is not None and not (final_turn and message.truncated):
            recovery = await self.k.review.review(message, turn, self.run)
            if recovery is not None:
                await self._continue(turn, recovery)
                return None
        if final_turn:
            # The answer-only turn past the budget: its text, else legacy's degraded tail. No turn is left
            # for queued input.
            journaled = self.run.slots.get(CUT_SHORT_SLOT)
            reason: CutShort = cast("CutShort", journaled) if journaled in CUT_SHORT_REASONS else "turns"
            if not message.text.strip():
                return await self._turns_exhausted(turn, answered_turn=turn, reason=reason)
            text = shortened(message.text) if message.truncated else message.text
            return await self._complete_with(turn, text, reason="cut_short", truncated_by=reason)
        if self.run.queued_inputs:
            await self._consume(turn, "turn_end", end_turn=True)
            return None
        cut_off = await self._cut_off_before(turn)
        if cut_off:
            # The model resumed exactly where it was cut off: joined as-is.
            return await self._complete_with(
                turn, cut_off + message.text, reason="truncation_continued"
            )
        # G-05: the run says it is done; its verify gate checks, and may send it back to work.
        verified, recovery = await self._verified(message.text, may_continue=not is_synthetic(response))
        if recovery is not None:
            await self._continue(turn, recovery, verified)
            return None
        text_ref = await self._put(message.text.encode("utf-8"))
        # The ending's texts are the model's alone, so a director that answers with a turn of its own (/run) names
        # its answer, or the presenter would find none.
        own_answer = message.text if is_synthetic(response) else None
        events = [
            NewEvent(
                "message.completed",
                {"role": "assistant", "textRef": text_ref, "isFinal": True},
            ),
            NewEvent("turn.completed", {"turn": turn}),
            *verified,
            *(await self._finalize("answered", answer=own_answer)),
        ]
        if not await self._completes(events, Complete("answered"), answer=message.text):
            await self._consume(turn, "turn_end", end_turn=True)
            return None
        self.log.info("run.complete", outcome="answered", turn=turn)
        return Disposition(
            "completed", transcript=tuple(await self.renderer.render(self.session))
        )

    async def _truncated_turns(self, turn: int) -> int:
        """How many turns in a row, ending at ``turn``, the model's response was truncated."""
        count = 0
        while turn >= 1:
            response = self.run.response_for(turn)
            if response is None or not decode_assistant(await self.renderer.blob(response.message_ref)).truncated:
                break
            count += 1
            turn -= 1
        return count

    async def _cut_off_before(self, turn: int) -> str:
        """The texts of the truncated, continued no-tool turns right before ``turn``."""
        parts: list[str] = []
        previous = turn - 1
        while previous >= 1:
            response = self.run.response_for(previous)
            if response is None or response.tool_calls:
                break
            earlier = decode_assistant(await self.renderer.blob(response.message_ref))
            if not earlier.truncated:
                break
            parts.insert(0, earlier.text)
            previous -= 1
        return "".join(parts)

    async def _verified(self, answer: str, *, may_continue: bool) -> tuple[list[NewEvent], str | None]:
        """The run's verification as it says it is done (G-05): the events that ride the ending, and the message
        that sends it back to work when its gate lets a failed round continue (``None``: it ends)."""
        if self.verifier is None:
            return [], None
        round_ = verify_round(self.run.slots)
        try:
            verification = await self.verifier.verify(answer, round_=round_, may_continue=may_continue)
        except (LeaseLost, OwnershipLost):
            raise
        except Exception as exc:
            # A check that breaks is a check that did not pass: the run is never verified by an error.
            self.log.warning("gate.verify_failed", errorType=type(exc).__name__)
            verification = Verification(
                (CheckResult("command", False, None, "The checks could not be run because of an internal error."),),
                round_,
            )
        self.log.info(
            "gate.verified",
            status=verification.status,
            round=round_,
            continuing=verification.continuing,
            failed=[c.kind for c in verification.checks if not c.passed],
        )
        if not verification.continuing:
            return [verification.event()], None
        saved = NewEvent("loop.state_saved", {"slots": {VERIFY_SLOT: round_}})
        return [verification.event(), saved], verification.recovery()

    async def _continue(self, turn: int, recovery: str, events: Sequence[NewEvent] = ()) -> None:
        """The turn ends without an answer: the recovery message is the next
        turn's input (the fold reads a completed no-tool turn as continued)."""
        _ = await self._commit(
            "continue",
            [
                *events,
                NewEvent("turn.completed", {"turn": turn}),
                NewEvent(
                    "message.completed",
                    {
                        "role": "user",
                        "textRef": await self._put(recovery.encode("utf-8")),
                        "isFinal": False,
                        "injected": True,
                    },
                ),
            ],
        )
        self.log.info("run.continue", turn=turn, reason="response_review")

    def _takes_final_answer_turn(self, turn: int) -> bool:
        """Legacy's ``_finish_after_max_turns``: one answer-only turn past the
        budget, only when the last turn called tools (the model has not seen
        their results), and only once per run."""
        if FINAL_ANSWER_SLOT in self.run.slots:
            return False
        previous = self.run.response_for(turn - 1)
        return bool(previous and previous.tool_calls)

    def _answer_only(self, sampled: SampledTurn, turn: int) -> SampledTurn:
        """The answer-only turn keeps only calls that end the run (``final_answer``);
        the rest are never executed, so the reply's text is the answer."""
        calls = sampled.message.tool_calls or []
        ends_run = self.tools.ends_run if isinstance(self.tools, TerminalTools) else None
        kept = [c for c in calls if ends_run is not None and ends_run(c.name)]
        if len(kept) == len(calls):
            return sampled
        self.log.info("run.final_answer_turn", turn=turn, toolCallsIgnored=len(calls) - len(kept))
        update: dict[str, object] = {"tool_calls": kept or None}
        if not kept:
            # Text written before a call the turn was told not to make is narration ("I'll check …"), not an
            # answer (main #4379): the turn ends as an empty one does, on the run's degraded answer.
            content = sampled.message.content
            update["content"] = "" if isinstance(content, str) else [p for p in content if not isinstance(p, TextPart)]
        return replace(sampled, message=sampled.message.model_copy(update=update))

    async def _model_failed(self, failed: ModelFailed) -> Disposition:
        self.log.warning("model.failed", errorType=_error_type(failed.error_type))
        if self._final_turn is not None:
            # A failed answer-only turn falls back to what the run already has, as legacy's does.
            return await self._turns_exhausted(self._final_turn, reason=self._cut_short or "turns")
        return await self._terminate(Fail(_error_type(failed.error_type)), "failed", "error")

    async def _turns_exhausted(
        self, turn: int, *, answered_turn: int | None = None, reason: CutShort = "turns"
    ) -> Disposition:
        """The run's answer when its limit ran out and no answer-only turn wrote one: past its turns, the last
        substantive text as a degraded answer (the legacy loops' tail); otherwise, or with none, what it had
        gathered, written without a model (BUDGETEND). The run completes either way, flagged as cut short."""
        texts, calls = await self._assistant_turns()
        answer = degraded_answer(next((t for t in reversed(texts) if t.strip()), "")) if reason == "turns" else None
        if answer is None:
            self.log.warning(
                "run.cut_short_fallback", reason=reason, maxTurns=self.claim.spec.budgets.max_turns, toolCalls=len(calls)
            )
            answer = cut_short_fallback(reason, [_model_tool_name(c.name) for c in calls])
        done = await self._complete_with(
            turn - 1 if answered_turn is None else answered_turn, answer, reason="cut_short", truncated_by=reason
        )
        assert done is not None, "a run out of turns completes whatever is queued"
        return done

    async def _complete_with(
        self,
        turn: int,
        answer: str,
        *,
        reason: str,
        events: Sequence[NewEvent] = (),
        confidence: str | None = None,
        truncated_by: CutShort | None = None,
    ) -> Disposition | None:
        """Complete ``answered`` with an answer that is not the turn's own text;
        ``None``: queued input turned it into a follow-up turn (``events`` kept)."""
        # No turn is left to take queued input after a limit ran out.
        leaves = truncated_by is not None
        response = self.run.response_for(turn)
        end_turn = response is None or not response.tool_calls
        if self.run.queued_inputs and not leaves:
            await self._consume(turn, "turn_end", events, end_turn=end_turn)
            return None
        ending: list[NewEvent] = []
        if self.run.turn_completed < turn:
            ending.append(NewEvent("turn.completed", {"turn": turn}))
        ending.append(
            NewEvent(
                "message.completed",
                {
                    "role": "assistant",
                    "textRef": await self._put(answer.encode("utf-8")),
                    "isFinal": True,
                },
            )
        )
        # A terminal tool's answer or a cut-short one is checked as it stands: no turn is left to work on.
        ending.extend((await self._verified(answer, may_continue=False))[0])
        ending.extend(
            await self._finalize("answered", answer=answer, confidence=confidence, truncated_by=truncated_by)
        )
        events = await self._landed(events, ending)
        if not await self._completes(
            [*events, CommitUnit(tuple(ending))],
            Complete("answered", leaves_input=leaves, truncated_by=truncated_by),
            answer=answer,
            confidence=confidence,
        ):
            await self._consume(turn, "turn_end", events, end_turn=end_turn)
            return None
        self.log.info(
            "run.complete",
            outcome="answered",
            turn=turn,
            reason=reason,
            truncatedBy=truncated_by,
            maxTurns=self.claim.spec.budgets.max_turns,
            leftInputs=len(self.run.queued_inputs) if leaves else 0,
        )
        return Disposition(
            "completed", transcript=tuple(await self.renderer.render(self.session))
        )

    # ---- tools ----

    async def _plan(self, turn: int) -> list[_Planned]:
        response = self.run.response_for(turn)
        assert response is not None
        message = decode_assistant(await self.renderer.blob(response.message_ref))
        await self._present_rest(turn, message, synthetic=is_synthetic(response))
        self._turn_text = message.text
        self._turn_cut = message.cut
        calls = {c.id: c for c in message.tool_calls or []}
        planned: list[_Planned] = []
        renamed: Mapping[str, str] = {}
        if self.k.prepare_calls is not None:
            names = [
                self.tool_lists.unwrap(calls.get(inv.call_id) or ToolCall(id=inv.call_id, name=inv.name)).name
                for inv in self.run.invocations_for(turn)
                if inv.state != "completed"
            ]
            if names:
                renamed = await self.k.prepare_calls(names) or {}
        # After the calls' tools are built: the policy reads each one's own rules (an MCP server's per-person rules).
        await self.effects.refresh()
        for inv in self.run.invocations_for(turn):
            if inv.state == "completed":
                continue
            raw = calls.get(inv.call_id) or ToolCall(id=inv.call_id, name=inv.name)
            call = self.tool_lists.unwrap(raw)
            if raw.name == CALL_TOOL and call.name != CALL_TOOL and isinstance(raw.arguments.get("name"), str):
                self._called_as[inv.invocation_id] = str(raw.arguments["name"])
            if call.name in renamed:
                call = call.model_copy(update={"name": renamed[call.name]})
            if message.truncated and inv.state == "pending":
                planned.append(_Planned(inv, call, UNKNOWN_TOOL, "cut", False))
                self.log.info("tool.recovery", invocationId=inv.invocation_id, choice="cut", cut=message.cut)
                continue
            delegated = self._delegated(inv, call)
            if delegated is not None:
                planned.append(delegated)
                continue
            refusal = await self.tool_lists.refusal(call, self.session) if inv.state == "pending" else None
            if refusal is not None:
                planned.append(_Planned(inv, call, UNKNOWN_TOOL, "stale", False, refusal))
                continue
            edit = self._approved_edit(inv)
            if edit is not None and edit.arguments is not None:
                call = call.model_copy(update={"arguments": dict(edit.arguments)})
                self._edited[inv.invocation_id] = edit.arguments
            described = self.invoker.resolve(call)
            effect = described or UNKNOWN_TOOL
            if described and self._decides_now(inv, edit):
                await self.effects.consult(call, effect)
            mode: _Mode = self._mode(inv, call, effect) if described else "run"
            if mode == "connect":
                skipped = await self._skipped_connect(call)
                if skipped is not None:
                    planned.append(_Planned(inv, call, effect, "stale", True, skipped))
                    self.log.info("tool.connect_skipped", invocationId=inv.invocation_id, toolId=effect.tool_id)
                    continue
            planned.append(
                _Planned(
                    inv,
                    call,
                    effect,
                    # A tool the run does not have only ever gets an error outcome.
                    mode,
                    described is not None,
                    need=self._connect_need(call) if mode == "connect" else None,
                )
            )
            fields = {
                "invocationId": inv.invocation_id,
                "toolId": effect.tool_id,
                "effect": effect.effect,
                "state": inv.state,
                "choice": mode if described else "not_available",
            }
            if inv.state == "pending" and mode == "run" and described:
                self.log.debug("tool.plan", **fields)
            else:
                self.log.info("tool.recovery", **fields)
        return planned

    @staticmethod
    def _decides_now(inv: Invocation, edit: ApprovedEdit | None) -> bool:
        """Whether ``_mode`` decides this call by the run's policy, so a policy that asks outside the run (G-40) is
        asked first: a new call, a connected one (connecting answers only the wait), and one a person approved with
        their own arguments (AP-06). Each is decided in an attempt that may never have asked about it."""
        if inv.state == "pending":
            return True
        if inv.state != "approved" or inv.intent_attempt_id is not None:
            return False
        return is_connect_interaction(inv.interaction_id) or (edit is not None and edit.arguments is not None)

    async def _skipped_connect(self, call: ToolCall) -> tuple[str, ToolErrorCode] | None:
        """The answer to a call of an item the person already chose not to connect in this task, its parent or a
        sub-agent (skipped, or not in time): the run goes on without it and nobody is asked again (WP-64). None:
        ask, as for its first call."""
        gate = self.k.connect
        need = gate.need(call) if gate is not None else None
        if gate is None or need is None:
            return None
        # The tree's skips as the claim names them (a parent's, a sibling's, a sub-agent's), and this run's own since.
        if need.instance_id in self.claim.skipped_connects or need.instance_id in skipped_connects(
            await self._read(), self.claim.run_id
        ):
            return gate.declined(call), "auth"
        return None

    def _connect_need(self, call: ToolCall) -> ConnectNeed | None:
        if self.k.connect is None or not self.claim.journal_accepts(CONNECT_PAUSES):
            return None
        return self.k.connect.need(call)

    def _policy_mode(self, inv: Invocation, call: ToolCall, effect: ToolEffect) -> _Mode:
        decision = self.effects.approve(call, effect)
        if decision == ALLOW_WITH_REDACTION:
            self._redacting.add(inv.invocation_id)
        if runs_unasked(decision):
            return "run"
        return "ask" if decision == "ask" else "deny"

    def _mode(self, inv: Invocation, call: ToolCall, effect: ToolEffect) -> _Mode:
        match inv.state:
            case "pending":
                mode = self._policy_mode(inv, call, effect)
                # Ask to connect only for a call that would be made: a rule's (or the plan gate's) refusal answers
                # now, and a dry run's shadowed call never uses the credential (G-03, G-04 beside G-12).
                if mode != "deny" and not self.effects.shadowed(effect) and self._connect_need(call) is not None:
                    return "connect"
                return mode
            case "awaiting_approval" if is_connect_interaction(inv.interaction_id):
                if inv.interaction_id is not None:
                    self._open_connects.add(inv.interaction_id)
                return "waiting"
            case "approved" if is_connect_interaction(inv.interaction_id) and inv.intent_attempt_id is None:
                # Connected while this attempt's tools were already assembled: the next attempt loads the credential.
                if inv.interaction_id in self._open_connects:
                    return "reassemble"
                # Connecting answers only the connect wait: the call still goes through the run's rules.
                return self._policy_mode(inv, call, effect)
            case "approved":
                if inv.intent_attempt_id is None:
                    edit = self._approved_edit(inv)
                    # An approval covers the person's own arguments, never ones a rule refuses outright; an edit
                    # nobody can read runs nothing, least of all the arguments the person changed.
                    if edit is not None and (edit.arguments is None or self.effects.approve(call, effect) == "deny"):
                        return "deny"
                    return "run"
                # An operator's re-call journals its own intent, so a crash during it leaves
                # two intents and is parked again, never re-called (P2.9 FB-2, for every tool).
                if is_unknown_interaction(inv) and effect.needs_intent_commit:
                    return "recall"
                return "retry"
            case "intent":
                if not effect.needs_intent_commit:
                    return "run"
                if self._reconcilable(effect):
                    return "reconcile"
                return self.effects.recover(effect)
            case "unknown":
                return "unknown" if inv.interaction_id is None else "waiting"
            case "rejected":
                if is_unknown_interaction(inv):
                    if inv.decision == OPERATOR_APPLIED:
                        return "confirm"
                    if inv.decision == OPERATOR_ABANDONED:
                        return "abandon"
                return "reject"
            case _:
                return "waiting"

    def _reconcilable(self, effect: ToolEffect) -> bool:
        reconciler = self.k.reconciler
        return (
            reconciler is not None
            and effect.needs_intent_commit
            and not effect.idempotent
            and reconciler.covers(effect.tool_id)
        )

    async def _dispatch(self, turn: int) -> Disposition | None:
        if self.k.shaper is not None:
            await self.k.shaper.enter(turn, self.run)
        planned = await self._plan(turn)
        connected = [p.inv.invocation_id for p in planned if p.mode == "reassemble"]
        if connected:
            self.log.info("tool.connect_reassemble", invocationIds=connected)
            return await self._yield("capability_wait")
        abandoned = [p for p in planned if p.mode == "abandon"]
        if abandoned:
            self.log.warning(
                "tool.abandoned",
                invocationIds=[p.inv.invocation_id for p in abandoned],
                reason=ABANDONED_ERROR,
            )
            return await self._terminate(
                Fail(ABANDONED_ERROR, "an operator abandoned an effect whose outcome was unknown"),
                "failed",
            )
        pending = [p for p in planned if p.mode == "spawn"]
        # A joined child's files land before anything else is admitted, so the
        # next wave's forks hold them (P6.4).
        for p in planned:
            if p.mode == "joined":
                _ = await self._merge_child(p)
        held: list[_Planned] = []
        if pending:
            held = await self._delegate(turn, pending)
            if len(held) < len(pending):
                # Node wrote `child.spawned`, or a refusal was journaled: the next fold sees it.
                return None
        joining = [p for p in planned if p.mode == "joining"]
        if held and not joining:
            # Nothing it waits on runs: such a wait would never be woken.
            await self._release_held(held)
            return None
        blocked = bool(joining or held)
        effects = [
            p
            for p in planned
            if p.mode in ("run", "retry", "recall")
            and p.known_tool
            and p.effect.needs_intent_commit
            # A dry run's shadowed call leaves nothing behind: it commits with the reads, simulated (G-03).
            and not self.effects.shadowed(p.effect)
        ]
        reconciling = [p for p in planned if p.mode == "reconcile"]
        if self.k.yield_before_effects and (
            effects or reconciling or any(p.mode in ("ask", "connect") for p in planned)
        ):
            return await self._yield("promotion")
        reads = [p for p in planned if p.mode in ("run", "retry") and p not in effects]
        outcomes = [
            CommitUnit(tuple(await self._refusal(p)))
            for p in planned
            if p.mode in ("deny", "reject")
        ]
        cut = [p for p in planned if p.mode == "cut"]
        if cut:
            offered = {schema.name for schema in self.tools.schemas() or []}
            note = truncated_call_note(
                await self._truncated_turns(turn), self._turn_cut, can_call_tool=offered.__contains__
            )
            for p in cut:
                outcomes.append(
                    CommitUnit(
                        (
                            await self.effect_log.outcome_event(
                                p.inv.invocation_id, ToolExecution(status="error", content=note)
                            ),
                        )
                    )
                )
        for p in planned:
            if p.mode == "stale" and p.refusal is not None:
                text, code = p.refusal
                outcomes.append(
                    CommitUnit(
                        (
                            await self.effect_log.outcome_event(
                                p.inv.invocation_id, ToolExecution(status="error", content=text, error_code=code)
                            ),
                        )
                    )
                )
        for p in planned:
            if p.mode == "joined":
                outcomes.append(CommitUnit(tuple(await self._joined(p))))
            elif p.mode == "confirm":
                outcomes.append(CommitUnit(tuple(await self._confirmed(p))))
        outcomes = [unit for unit in outcomes if unit.events]
        waiting = [p for p in planned if p.mode in ("ask", "waiting", "connect")]
        if reads or outcomes:
            settled = await self._settled(outcomes, await self._run_reads(reads))
            await afailpoint("worker.tool.before_outcome_commit")
            if self._needs_input and not effects and not waiting and not blocked:
                return await self._complete_needs_input(turn, settled)
            terminal = self._terminal_answer(turn)
            if terminal is not None and not effects and not waiting and not blocked:
                return await self._complete_with(
                    turn,
                    terminal[0],
                    reason="terminal_tool",
                    events=settled,
                    confidence=terminal[1],
                )
            _ = await self._commit("reads", settled)
        unknown: list[_Planned] = [p for p in planned if p.mode == "unknown"]
        for p in reconciling:
            if not await self._reconcile(p):
                unknown.append(p)
        for p in effects:
            await self._sync()
            if self.run.cancel_requested:
                return await self._terminate(Cancelled(), "cancelled", "stopped")
            if await self._run_effect(p):
                continue
            if not (self._reconcilable(p.effect) and await self._reconcile(p)):
                unknown.append(p)
        if unknown or waiting:
            granted = await self._granted_since_plan([p for p in waiting if p.mode == "ask"])
            if granted:
                # Still pending: the next plan runs them under the grant, through the same rules as any call.
                waiting = [p for p in waiting if p.inv.invocation_id not in granted]
                if not unknown and not waiting:
                    return None
            asking = [p for p in waiting if p.mode in ("ask", "connect")]
            # A connect is never held in this attempt: its tools predate the credential the owner adds (G-12).
            connecting = any(
                p.mode == "connect" or is_connect_interaction(p.inv.interaction_id) for p in waiting
            )
            if self.bridge is not None and not unknown and not connecting:
                if asking:
                    await afailpoint("worker.batch.before_ask")
                    _ = await self._commit("ask", self._requested_events(asking))
                    self.log.info(
                        "tool.approval_requested",
                        asking=len(asking),
                        interactionIds=[
                            interaction_id(p.inv.invocation_id, "approve") for p in asking
                        ],
                    )
                # The loop holds the asks it now finds open (cursor ``suspended``).
                return None
            return await self._suspend([*unknown, *asking])
        if held:
            # A later wave waits on this one: suspend until its children end.
            return await self._suspend_join()
        if joining:
            # Back to the loop: the cursor suspends on `join` once nothing else is open.
            return None
        if self._needs_input:
            return await self._complete_needs_input(turn, [])
        terminal = self._terminal_answer(turn)
        if terminal is not None:
            return await self._complete_with(
                turn, terminal[0], reason="terminal_tool", confidence=terminal[1]
            )
        return None

    def _terminal_answer(self, turn: int) -> tuple[str, str | None] | None:
        """The last terminal answer in ``toolCalls`` order, as the legacy loop
        takes it: this attempt's, or one an earlier attempt journaled."""
        journaled = self.run.slots.get(TERMINAL_SLOT)
        earlier = journaled if isinstance(journaled, dict) else {}
        answers: list[tuple[str, str | None]] = []
        for inv in sorted(self.run.invocations_for(turn), key=lambda i: i.index):
            if inv.invocation_id in self._answers:
                answers.append(self._answers[inv.invocation_id])
            elif earlier.get("invocationId") == inv.invocation_id:
                confidence = earlier.get("confidence")
                answers.append(
                    (
                        str(earlier.get("answer") or ""),
                        confidence if isinstance(confidence, str) else None,
                    )
                )
        return answers[-1] if answers else None

    async def _landed(self, events: Sequence[NewEvent], ending: Sequence[NewEvent]) -> list[NewEvent]:
        """``events`` when one commit holds them with the run's ``ending``; otherwise they are committed
        first and the ending goes alone. A completion Node refuses (input was queued) is then never half
        journaled, so what follows it commits no outcome twice."""
        if len(self._riding) + len(events) + len(ending) <= self.k.max_commit_events:
            return list(events)
        _ = await self._commit("reads", list(events))
        return []

    async def _complete_needs_input(
        self, turn: int, events: Sequence[NewEvent]
    ) -> Disposition | None:
        """A question ends the turn: the outcomes and the completion commit
        together. Input the user queued meanwhile is the next turn's instead."""
        if not self.run.queued_inputs:
            ending = [
                NewEvent("turn.completed", {"turn": turn}),
                *(await self._finalize("needs_input")),
            ]
            events = await self._landed(events, ending)
            if await self._completes([*events, CommitUnit(tuple(ending))], Complete("needs_input")):
                self.log.info("run.complete", outcome="needs_input", turn=turn)
                return Disposition(
                    "completed",
                    "needs_input",
                    tuple(await self.renderer.render(self.session)),
                )
        self._needs_input = False
        await self._consume(turn, "turn_end", events)
        return None

    async def _completes(
        self,
        events: Sequence[NewEvent | CommitUnit],
        transition: Complete,
        *,
        answer: str | None = None,
        confidence: str | None = None,
    ) -> bool:
        """``False``: Node refused, the user queued input this worker had not read."""
        transition = await self._as_child(transition, answer, confidence)
        try:
            _ = await self._commit("complete", events, transition)
        except InputPending:
            for _ in range(STALE_READ_RETRIES + 1):
                await self._sync()
                if self.run.queued_inputs:
                    self.log.info(
                        "run.follow_up",
                        reason="input_pending",
                        queued=len(self.run.queued_inputs),
                    )
                    await self._after_committed(landed=False)
                    return False
            await self._after_committed(landed=False)
            raise
        except BaseException:
            await self._after_committed(landed=False)
            raise
        await self._after_committed(landed=True)
        return True

    async def _consume(
        self,
        turn: int,
        boundary: str,
        keep: Sequence[NewEvent] = (),
        *,
        end_turn: bool = False,
    ) -> None:
        """Takes every queued input into the transcript (REPORT 6.2.2, D19);
        ``keep``: the outcomes the turn settled, committed with it."""
        pending = list(self.run.queued_inputs)
        events = list(keep)
        if end_turn and self.run.turn_completed < turn:
            events.append(NewEvent("turn.completed", {"turn": turn}))
        rendered: list[NewEvent] = []
        counts: dict[int, tuple[int, int]] = {}
        for seq in pending:
            data: dict[str, JsonValue] = {"messageSeq": seq}
            queued = self.session.queued_inputs.get(f"{self.claim.run_id}:{seq}")
            if queued is not None and queued.has_references:
                counts[seq] = (len(queued.attachments), len(queued.record_ids))
                content = await self._render_input(seq, queued)
                if content is not None:
                    data["contentRef"] = await self._put(encode_user_parts(content.parts))
                    rendered.extend(content.events)
            events.append(NewEvent("input.consumed", data))
        events.extend(rendered)
        await afailpoint("worker.input.before_consume_commit")
        _ = await self._commit("input", events)
        for seq in pending:
            attachments, records = counts.get(seq, (0, 0))
            self.log.info(
                "input.consumed",
                messageSeq=seq,
                boundary=boundary,
                turn=turn,
                attachments=attachments,
                records=records,
            )
        await afailpoint("worker.input.after_consume_commit")

    async def _render_input(self, seq: int, queued: QueuedInput) -> RenderedInput | None:
        """The parts a steering message with references gives the model; ``None``:
        no renderer, so the model gets its text alone."""
        if self.k.inputs is None:
            self.log.warning("input.references_unrendered", messageSeq=seq)
            return None
        text = (
            (await self.renderer.blob(queued.text_ref)).decode("utf-8")
            if queued.text_ref is not None
            else ""
        )
        return await self.k.inputs.render(
            SteeringInput(
                message_seq=seq,
                text=text,
                attachments=tuple(
                    cast("Mapping[str, JsonValue]", a.model_dump(mode="json", by_alias=True, exclude_none=True))
                    for a in queued.attachments
                ),
                record_ids=tuple(queued.record_ids),
            )
        )

    async def _refusal(self, p: _Planned) -> list[NewEvent]:
        edit = self._approved_edit(p.inv) if p.mode == "deny" else None
        if edit is not None and edit.arguments is None:
            rule, reason = EDIT_UNREADABLE
        elif p.mode == "deny":
            rule, reason = self.effects.denial(p.call, p.effect)
        elif p.inv.intent_attempt_id is not None:
            reason, rule = "effect status unknown; not retried", "interaction_denied"
        elif is_connect_interaction(p.inv.interaction_id) and self.k.connect is not None:
            rule, reason = "interaction_denied", self.k.connect.declined(p.call)
        else:
            rule, reason = approval_denial(self.said(p.inv.interaction_id) if p.inv.interaction_id else None)
        return await self.effect_log.refusal_events(p.inv.invocation_id, rule, reason, tool_id=p.effect.tool_id)

    async def _run_reads(self, reads: list[_Planned]) -> list[_Read]:
        async def one(p: _Planned) -> tuple[list[NewEvent], set[str], bool]:
            call = self._effect_call(p)
            # A tool the run does not have goes to the port too: it refuses the
            # call with the tool hooks around the refusal, as legacy runs them.
            span = self._tool_span(p)
            try:
                execution = await self.invoker.read(call, self._ctx(p.inv))
            except BaseException as exc:
                span.end(error_type=type(exc).__name__)
                raise
            span.end(error_type=_tool_error(execution))
            self._needs_input = self._needs_input or execution.needs_input
            keys: set[str] = set()
            for update in self._took(p, execution):
                slots.update(update)
                keys.update(update)
            log = self.effect_log
            events = [] if p.inv.state == "intent" else [log.intent_event(call)]
            events.append(await log.outcome_event(p.inv.invocation_id, execution))
            events.extend(execution.events)
            return events, keys, execution.needs_input or execution.answer is not None

        # Filled as the reads finish: a slot several of them set holds the last finisher's value.
        slots: dict[str, JsonValue] = {}
        results = await self._unless_cancelled(asyncio.gather(*(one(p) for p in reads)))
        return [
            _Read(CommitUnit(tuple(events), {key: slots[key] for key in sorted(keys)}), ends)
            for events, keys, ends in results
        ]

    async def _settled(self, outcomes: list[CommitUnit], reads: list[_Read]) -> list[NewEvent]:
        """The batch's outcomes as the events its last commit carries; what one commit cannot hold
        is committed first. A read commits whole with the slots it set, so a crash between two
        commits leaves each read journaled once or not at all."""
        # Reads that end the run leave room for its completion beside them.
        reserve = ENDING_RESERVE if any(read.ends for read in reads) else 0
        room = max(2, self.k.max_commit_events - len(self._riding) - reserve)
        parts = pack([*outcomes, *(read.unit for read in reads)], room)
        if len(parts) > 1:
            # That a read asked the user or gave the run's answer is known in full only to this
            # attempt: its outcome commits with the completion it leads to, or a crash before that
            # commit would resume a run that no longer ends there.
            parts = pack(
                [
                    *outcomes,
                    *(read.unit for read in reads if not read.ends),
                    *(read.unit for read in reads if read.ends),
                ],
                room,
            )
        for part in parts[:-1]:
            _ = await self._commit("reads", part)
        return parts[-1] if parts else []

    def _took(
        self, p: _Planned, execution: ToolExecution
    ) -> list[Mapping[str, JsonValue]]:
        """The slots the call's outcome journals: its own, and a terminal answer."""
        slots = [execution.slots] if execution.slots else []
        if execution.answer is not None:
            self._answers[p.inv.invocation_id] = (execution.answer, execution.confidence)
            terminal: dict[str, JsonValue] = {
                "invocationId": p.inv.invocation_id,
                "answer": execution.answer,
            }
            if execution.confidence is not None:
                terminal["confidence"] = execution.confidence
            slots.append({TERMINAL_SLOT: terminal})
            self.log.info(
                "tool.terminal", invocationId=p.inv.invocation_id, toolId=p.effect.tool_id
            )
        return slots

    async def _run_effect(self, p: _Planned) -> bool:
        """Execute a write/execute call; ``False`` when its effect is unknown."""
        self.lease.check()
        ctx = self._ctx(p.inv)
        call = self._effect_call(p)
        if p.mode == "run" and p.inv.state == "pending":
            # The policy allowed it when it was planned; nobody was asked.
            call = self.effects.allowed(call)
        write_intent = p.mode in ("run", "recall")

        edited = self._edited.get(p.inv.invocation_id)

        def settle(execution: ToolExecution) -> ToolExecution:
            settled = self._settle(p, execution)
            return settled if edited is None else with_edit_note(settled, edited)

        # An idempotent execute (code in the run's own sandbox, P5.11) is raced against Stop like a model call: its
        # sandbox goes with it, and a retry answers from its receipt, so a /run or the agent's code never outlives
        # Stop (SLASH3, LCFIX-4). Any other effect is left to finish: stopping it midway could leave it half applied.
        stoppable = p.effect.idempotent and p.effect.effect == "execute"
        span = self._tool_span(p)
        try:
            execution = await self._perform(p, call, ctx, write_intent, settle, stoppable=stoppable)
        except BaseException as exc:
            span.end(error_type=type(exc).__name__)
            raise
        span.end(error_type="effect_unknown" if execution is None else _tool_error(execution))
        return execution is not None

    async def _perform(
        self,
        p: _Planned,
        call: EffectCall,
        ctx: ToolContext,
        write_intent: bool,
        settle: Callable[[ToolExecution], ToolExecution],
        *,
        stoppable: bool,
    ) -> ToolExecution | None:
        if self._reconcilable(p.effect):
            # The write carries the effect's tag so a reconcile can find it by a read.
            with stamped_effect(ctx.idempotency_key):
                execution = await self.invoker.perform(
                    call, ctx, write_intent=write_intent, settle=settle
                )
        elif stoppable:
            execution = await self._unless_cancelled(
                self.invoker.perform(call, ctx, write_intent=write_intent, settle=settle)
            )
        else:
            execution = await self.invoker.perform(
                call, ctx, write_intent=write_intent, settle=settle
            )
        return execution

    async def _reconcile(self, p: _Planned) -> bool:
        """Decide an unknown effect by a read (P2.9); ``False``: a person decides
        (the verdict rides with the suspension)."""
        reconciler = self.k.reconciler
        assert reconciler is not None
        await self._sync()
        inv = self.run.invocations.get(p.inv.invocation_id, p.inv)
        ctx = self._ctx(inv)
        effect = unknown_effect_for(
            inv,
            p.call,
            p.effect,
            effect_key=ctx.idempotency_key,
            run_id=self.claim.run_id,
            lease_s=self.claim.lease_ms / 1000,
            now=self.store_time.now(),
        )
        verdict = with_recall_rule(await reconciler.reconcile(effect), inv)
        self.lease.check()
        self.log.info(
            "tool.reconciled",
            invocationId=inv.invocation_id,
            toolId=p.effect.tool_id,
            verdict=verdict.answer,
            reason=verdict.reason,
            lookups=verdict.lookups,
        )
        event = reconciled_event(inv.invocation_id, verdict)
        match verdict.answer:
            case "applied":
                evidence = verdict.evidence or ReconcileEvidence(None)
                await afailpoint("worker.reconcile.before_commit")
                _ = await self._commit(
                    "reconcile", [event, *await self._applied_events(p, evidence, "reconciler")]
                )
                return True
            case "not_applied":
                await afailpoint("worker.reconcile.before_commit")
                _ = await self._commit("reconcile", [event])
                await afailpoint("worker.reconcile.after_not_applied")
                p.mode = "recall"
                if await self._run_effect(p):
                    return True
                # The re-call's own effect is unknown: one more read decides, never another re-call.
                return await self._reconcile(p)
            case "still_unknown":
                self._verdicts[inv.invocation_id] = event
                return False

    async def _applied_events(
        self, p: _Planned, evidence: ReconcileEvidence, source: ReconcileSource
    ) -> list[NewEvent]:
        execution = self._settle(p, applied_execution(evidence, source))
        events = [
            await self.effect_log.outcome_event(p.inv.invocation_id, execution),
            *execution.events,
        ]
        if execution.slots:
            events.append(
                NewEvent("loop.state_saved", {"slots": cast("JsonValue", dict(execution.slots))})
            )
        return events

    async def _confirmed(self, p: _Planned) -> list[NewEvent]:
        """An operator resolved the unknown effect as applied: its outcome, never a re-call."""
        self.log.info(
            "tool.reconciled",
            invocationId=p.inv.invocation_id,
            toolId=p.effect.tool_id,
            verdict="applied",
            reason="operator",
            lookups=0,
        )
        return [
            reconciled_event(p.inv.invocation_id, operator_verdict(), "operator"),
            *await self._applied_events(p, ReconcileEvidence(OPERATOR_EVIDENCE), "operator"),
        ]

    def _settle(self, p: _Planned, execution: ToolExecution) -> ToolExecution:
        """What the outcome commit journals: the call's slots and a terminal answer."""
        self._needs_input = self._needs_input or execution.needs_input
        slots: dict[str, JsonValue] = {}
        for update in self._took(p, execution):
            slots.update(update)
        return replace(execution, slots=slots)

    def _effect_call(self, p: _Planned) -> EffectCall:
        call = self.invoker.effect_call(
            p.call, p.effect, p.inv.invocation_id, p.inv.args_digest
        )
        if p.inv.invocation_id not in self._redacting:
            return call
        return replace(call, redaction=self.effects.redaction(call) or POLICY_REDACTION)

    def _ctx(self, inv: Invocation) -> ToolContext:
        return self.invoker.context(
            inv.invocation_id, self._turn_text, inv.interaction_id, inv.args_digest,
            self._called_as.get(inv.invocation_id),
        )

    # ---- child runs (P6.1, K-L) ----

    def _delegated(self, inv: Invocation, call: ToolCall) -> _Planned | None:
        """A call that delegates to a child run: decide it, wait on it, or take its result."""
        delegation = self.k.delegation
        if delegation is None or not delegation.delegates(call):
            return None
        mode: _Mode
        match inv.state:
            case "joining":
                mode = "joining"
            case "joined":
                mode = "joined"
            case _:
                mode = "spawn"
        self.log.debug(
            "child.plan", invocationId=inv.invocation_id, state=inv.state, choice=mode
        )
        return _Planned(inv, call, delegation.effect(call.name), mode)

    async def _delegate(self, turn: int, pending: list[_Planned]) -> list[_Planned]:
        """Decide every pending delegation of the batch against one view: admit,
        refuse or hold. Returns the held ones (they wait on calls of the batch)."""
        delegation = self.k.delegation
        assert delegation is not None
        view = await self._delegation_view(turn)
        items = {d.invocation_id: d for d in view.batch}
        held: list[_Planned] = []
        refusals: list[CommitUnit] = []
        for p in pending:
            decision = delegation.decide(items[p.inv.invocation_id], view)
            if isinstance(decision, Wait):
                held.append(p)
                self.log.debug(
                    "child.held", invocationId=p.inv.invocation_id, waitsOn=list(decision.on)
                )
                continue
            if isinstance(decision, Refuse):
                self.log.info("child.declined", invocationId=p.inv.invocation_id)
                refusals.append(CommitUnit(tuple(await self._declined(p, decision.content))))
                continue
            try:
                admitted = await self._admit_child(decision)
            except ChildRefused as refused:
                self.log.warning(
                    "child.refused",
                    invocationId=p.inv.invocation_id,
                    reason=refused.reason.value,
                )
                # What the refusal means to the person, when the delegation says (CTL-6's notice); optional.
                noting = getattr(delegation, "refused", None)
                noted = tuple(noting(items[p.inv.invocation_id].call, refused)) if callable(noting) else ()
                refusals.append(
                    CommitUnit(
                        (
                            *await self.effect_log.refusal_events(
                                p.inv.invocation_id,
                                refused.reason.value,
                                # The calling model reads this: without the reason it retries the same spawn.
                                f"child run refused: {refused.reason.value}"
                                + (f" ({refused.detail})" if refused.detail else ""),
                            ),
                            *noted,
                        )
                    )
                )
                continue
            self.log.info(
                "child.spawned",
                invocationId=p.inv.invocation_id,
                childRunId=admitted.child_run_id,
                created=admitted.created,
            )
            await afailpoint("worker.spawn.after_admission")
        if refusals:
            _ = await self._commit("refusal", refusals)
        elif len(held) < len(pending):
            await self._sync()
        return held

    async def _declined(self, p: _Planned, content: JsonValue) -> list[NewEvent]:
        """A delegation the registry refused: an error outcome, no child."""
        execution = ToolExecution(status="error", content=content)
        return [
            self.effect_log.intent_event(self._effect_call(p)),
            await self.effect_log.outcome_event(p.inv.invocation_id, execution),
        ]

    async def _release_held(self, held: list[_Planned]) -> None:
        self.log.warning("child.wait_unrunnable", held=len(held))
        events = [
            CommitUnit(tuple(await self._declined(p, "waits on a sub-agent that is not running")))
            for p in held
        ]
        _ = await self._commit("refusal", events)

    async def _delegation_view(self, turn: int) -> DelegationView:
        """Every delegating call of the run so far, with what the journal holds of it."""
        delegation = self.k.delegation
        assert delegation is not None
        calls: dict[int, dict[str, ToolCall]] = {}
        batch: list[Delegated] = []
        earlier: list[Delegated] = []
        gathered: list[Gathered] = []
        for inv in sorted(self.run.invocations.values(), key=lambda i: (i.turn, i.index)):
            if inv.turn not in calls:
                response = self.run.response_for(inv.turn)
                message = (
                    decode_assistant(await self.renderer.blob(response.message_ref))
                    if response is not None
                    else None
                )
                calls[inv.turn] = {c.id: c for c in (message.tool_calls if message else None) or []}
            call = calls[inv.turn].get(inv.call_id) or ToolCall(id=inv.call_id, name=inv.name)
            if not delegation.delegates(call):
                # HANDOFF-CTX: what the run already gathered, so a child is not sent to fetch it again.
                if inv.turn < turn and inv.state == "completed" and inv.result_ref is not None:
                    outcome, _ = decode_result(await self.renderer.blob(inv.result_ref))
                    gathered.append(Gathered(call, inv.turn, inv.status, outcome))
                continue
            item = await self._delegated_item(inv, call)
            (batch if inv.turn == turn else earlier).append(item)
        return DelegationView(tuple(batch), tuple(earlier), dict(self.run.slots), tuple(gathered))

    async def _delegated_item(self, inv: Invocation, call: ToolCall) -> Delegated:
        state: DelegatedState = "pending"
        document: JsonValue | None = None
        outcome: JsonValue | None = None
        if inv.state == "joining":
            state = "joining"
        elif inv.state == "joined" or (inv.state == "completed" and inv.child_run_id):
            state = "joined"
            document, _ = await self._child_document(inv)
        elif inv.state == "completed":
            state = "refused"
        if inv.state == "completed" and inv.result_ref is not None:
            outcome, _ = decode_result(await self.renderer.blob(inv.result_ref))
        return Delegated(inv.invocation_id, call, inv.turn, state, document, outcome)

    async def _child_document(self, inv: Invocation) -> tuple[JsonValue, str]:
        """The child's result document and integrity label (Node's failure shape when unreadable)."""
        record = self.run.children.get(inv.child_run_id or "")
        content: JsonValue = {"output": "", "success": False, "error": "child_result_missing"}
        integrity = "internal"
        if record is not None and record.result_ref is not None:
            try:
                content = cast("JsonValue", json.loads(await self.content.get(record.result_ref)))
            except (ContentMissing, ValueError):
                self.log.warning(
                    "child.result_unreadable",
                    invocationId=inv.invocation_id,
                    childRunId=record.child_run_id,
                )
            label = (record.labels or {}).get("integrity")
            integrity = label if isinstance(label, str) else integrity
        return content, integrity

    async def _admit_child(self, request: ChildRequest) -> ChildAdmitted:
        children = self.k.children
        if children is None:
            raise ChildRefused(ChildRefusal.NOT_GRANTED, "no child admission here")
        self.lease.check()
        request = self._with_context(request)
        return await while_leased(
            lambda: children.spawn(self.claim.token, request),
            lease=self.lease,
            retry_s=self.k.journal_retry_s,
            sleep=self.k.sleep,
            on_retry=lambda retries: self.log.warning(
                "child.spawn_retry", retries=retries, retryS=self.k.journal_retry_s
            ),
        )

    def _with_context(self, request: ChildRequest) -> ChildRequest:
        """The child inherits what this run had read, as the ledger labels it (UX2), and the conversation grants it
        holds (FLOWASK), each when the journal accepts it; else it is admitted as before, counts its parent's work as
        unknown and asks again where the parent was allowed."""
        return self._with_pins(self._with_task(self._with_grants(self._with_label(request))))

    def _with_pins(self, request: ChildRequest) -> ChildRequest:
        """The parent's first-use MCP pins, for a journal that takes them; an older Node refuses a body field it does
        not know, and the child then lists each server at its own first use."""
        source = self.k.mcp_pins
        if request.mcp_pins is not None or source is None:
            return request
        pins = {instance: dict(tools) for instance, tools in source().items() if tools}
        if not pins:
            return request
        if not self.claim.journal_accepts(CHILD_MCP_PINS):
            self.log.info("child.mcp_pins_withheld", invocationId=request.invocation_id, reason="journal_lacks_feature")
            return request
        self.log.info("child.mcp_pins_carried", invocationId=request.invocation_id, servers=len(pins))
        return replace(request, mcp_pins=pins)

    def _with_task(self, request: ChildRequest) -> ChildRequest:
        """The task's parts only for a journal that keeps them; an older Node refuses a body field it does not know."""
        if request.task is None or self.claim.journal_accepts(CHILD_TASKS):
            return request
        return replace(request, task=None)

    def _with_grants(self, request: ChildRequest) -> ChildRequest:
        ledger = self.k.ledger
        if request.inherited_grants is not None or not isinstance(ledger, GrantsContext):
            return request
        grants = ledger.context_grants()
        if not grants:
            return request
        if not self.claim.journal_accepts(CHILD_GRANTS):
            self.log.info("child.grants_withheld", invocationId=request.invocation_id, reason="journal_lacks_feature")
            return request
        self.log.info("child.grants_inherited", invocationId=request.invocation_id, grants=len(grants))
        return replace(request, inherited_grants=grants)

    def _with_label(self, request: ChildRequest) -> ChildRequest:
        ledger = self.k.ledger
        if request.inherited_label is not None or not isinstance(ledger, LabelsContext):
            return request
        if not self.claim.journal_accepts(CHILD_LABELS):
            self.log.info("child.label_withheld", invocationId=request.invocation_id, reason="journal_lacks_feature")
            return request
        label = ledger.context_label(self.claim.run_id)
        if label is None:
            return request
        self.log.info(
            "child.label_inherited",
            invocationId=request.invocation_id,
            complete=label.get("complete") is True,
            sources=len(cast("list[object]", label.get("sources") or [])),
        )
        return replace(request, inherited_label=label)

    async def _joined(self, p: _Planned) -> list[NewEvent]:
        """The child's result as the delegating call's outcome, under the child's label;
        what the model reads of it is the registry's."""
        delegation = self.k.delegation
        assert delegation is not None
        document, integrity = await self._child_document(p.inv)
        workspace = await self._merge_child(p)
        shown = (
            await delegation.outcome(p.call, document)
            if workspace is None
            else await delegation.outcome(p.call, document, workspace=workspace)
        )
        execution = ToolExecution(
            status="error" if shown.error else "ok",
            content=shown.content,
            integrity=cast("Literal['trusted', 'internal', 'external']", integrity),
        )
        record = self.run.children.get(p.inv.child_run_id or "")
        self.log.info(
            "child.joined",
            invocationId=p.inv.invocation_id,
            childRunId=p.inv.child_run_id,
            outcome=record.outcome if record is not None else None,
            events=len(shown.events),
        )
        call = self._effect_call(p)
        return [
            self.effect_log.intent_event(call),
            await self.effect_log.outcome_event(p.inv.invocation_id, execution),
            *shown.events,
            *_merge_committed(workspace),
        ]

    async def _merge_child(self, p: _Planned) -> ChildMergeReport | None:
        """Offer a joined child's fork to this run's workspace, once per child.

        The port is idempotent per child (a re-claimed attempt gets the report
        the first merge made); a store outage is retried like a journal one.
        """
        port = self.k.child_workspaces
        child_run_id = p.inv.child_run_id
        if port is None or child_run_id is None:
            return None
        if child_run_id in self._merges:
            return self._merges[child_run_id]
        workspace_id = run_workspace(self.claim.spec, self.claim.session_id)
        if workspace_id is None:
            self._merges[child_run_id] = None
            return None
        record = self.run.children.get(child_run_id)
        child = JoinedChild(
            tenant_id=self.claim.org_id,
            session_id=self.claim.session_id,
            parent_run_id=self.claim.run_id,
            workspace_id=workspace_id,
            child_run_id=child_run_id,
            outcome=record.outcome if record is not None else None,
        )

        async def merge() -> ChildMergeReport | None:
            try:
                report = await port.merge(self.claim.token, child)
                # WP-56: a failed child's saved work reaches the person in its own folder, never merged (CW3).
                return report if report is None else await port.keep_unfinished(self.claim.token, child, report)
            except WorkspaceStoreUnavailable as exc:
                raise JournalUnavailable(str(exc)) from exc

        self.lease.check()
        await afailpoint("worker.join.before_merge")
        report = await while_leased(
            merge,
            lease=self.lease,
            retry_s=self.k.journal_retry_s,
            sleep=self.k.sleep,
            on_retry=lambda retries: self.log.warning(
                "child.workspace_retry", retries=retries, retryS=self.k.journal_retry_s
            ),
        )
        await afailpoint("worker.join.after_merge")
        self._merges[child_run_id] = report
        if report is not None:
            fields = {
                "invocationId": p.inv.invocation_id,
                "childRunId": child_run_id,
                "status": report.status,
                "revision": report.revision,
                "forkRevision": report.fork_revision,
                "merged": len(report.merged),
                "conflicts": len(report.conflicts),
                "repositories": len(report.repositories),
                "reason": report.reason,
                "unfinished": len(report.kept),
            }
            if report.reason == "writer_busy":
                self.log.warning("child.workspace_not_merged", **fields)
            else:
                self.log.info("child.workspace_merged", **fields)
        return report

    def _waits_on_children_only(self) -> bool:
        batch = self.run.invocations_for(self.run.turn)
        waiting = [i for i in batch if i.state not in ("completed",)]
        return bool(waiting) and all(i.state == "joining" for i in waiting)

    async def _suspend_join(self) -> Disposition | None:
        """Wait on the batch's children; ``None``: they all ended meanwhile (``JoinReady``)."""
        open_children = sum(1 for c in self.run.children.values() if not c.completed)
        await afailpoint("worker.batch.before_suspend")
        try:
            _ = await self._commit("suspend", [], Suspend("join"))
        except JoinReady:
            self.log.info("run.join_ready", children=open_children)
            await self._sync()
            return None
        self.log.info("run.suspend", reason="join", children=open_children)
        return Disposition("suspended", "join")

    async def _as_child(
        self,
        transition: Complete,
        answer: str | None = None,
        confidence: str | None = None,
    ) -> Complete:
        """A child completes with its result document for its parent (sub-agent shape)."""
        if self.claim.spec.run_kind != "child":
            return transition
        output = answer
        if output is None:
            texts = await self._assistant_texts()
            output = next((t for t in reversed(texts) if t.strip()), "")
        document = child_result_document(
            outcome=transition.outcome, output=output, confidence=confidence, truncated_by=transition.truncated_by
        )
        if self.k.child_result is not None:
            document = await self.k.child_result.shape(document, transition.outcome)
        ref = await self.content.put(canonical_json(cast("JsonValue", document)))
        return replace(
            transition,
            child_result=ChildResultRef(
                result_ref=ref, labels={"integrity": self.run.context_label}
            ),
        )

    # ---- transitions ----

    async def _granted_since_plan(self, asks: list[_Planned]) -> set[str]:
        """The asks a conversation grant now answers (FLOWGRANT, ADR-GRANT-01 D3), read from the stream just before
        they would be journaled: Node copies a grant given in another run of the conversation onto this run's stream
        and answers only the asks open then, so a copy that landed after this turn's plan (while its reads ran) would
        otherwise be read only after the run asked once more. Each invocation is deferred at most once an attempt, so
        a policy whose answer flips back can never keep a call from being asked."""
        asks = [p for p in asks if p.inv.invocation_id not in self._granted_before_ask]
        ledger = self.k.ledger
        if not asks or not isinstance(ledger, GrantingLedger) or not ledger.grants.enabled:
            return set()
        await self._sync()
        granted = {p.inv.invocation_id for p in asks if self.effects.approve(p.call, p.effect) != "ask"}
        if granted:
            self._granted_before_ask |= granted
            self.log.info("tool.approval_granted_before_ask", invocationIds=sorted(granted))
        return granted

    def _requested_events(self, asking: list[_Planned]) -> list[CommitUnit]:
        """One unit a call: its request commits with what explains it."""
        return [CommitUnit(tuple(self._requested(p))) for p in asking]

    def _requested(self, p: _Planned) -> list[NewEvent]:
        events: list[NewEvent] = []
        purpose = _purpose(p.mode)
        ix = interaction_id(p.inv.invocation_id, purpose)
        if purpose == "unknown":
            verdict = self._verdicts.pop(p.inv.invocation_id, None)
            if verdict is not None:
                events.append(verdict)
            events.append(self.effect_log.unknown_event(p.inv.invocation_id))
        binding = canonical_digest(
            {
                "invocationId": p.inv.invocation_id,
                "toolId": p.effect.tool_id,
                "argsDigest": p.inv.args_digest,
                "purpose": purpose,
            }
        )
        if purpose == "approve":
            # One constructor: the reason precedes the request in the same unit (WP-97).
            events.extend(approval_request(ix, binding, self._explained(ix, p)))
            return events
        if purpose == "connect" and p.need is not None:
            self._open_connects.add(ix)
            events.append(
                NewEvent(
                    "interaction.requested",
                    {"interactionId": ix, "kind": "connect", "bindingDigest": binding, "connect": p.need.journaled()},
                )
            )
            return events
        events.append(
            NewEvent(
                "interaction.requested",
                {"interactionId": ix, "kind": "approval", "bindingDigest": binding},
            )
        )
        return events

    def _input_schema(self, name: str) -> dict[str, Any] | None:
        """The input schema of the run's tool ``name``, as the model was shown it; None when it has none here."""
        return next((s.input_schema for s in self.tools.schemas() or [] if s.name == name), None)

    def _explained(self, ix: str, p: _Planned) -> Mapping[str, JsonValue] | None:
        """Why the policy asks, for the person deciding (UX1); judged on the same ledger as the decision. ``None``
        only for a journal that refuses the event."""
        reason: Mapping[str, JsonValue] | None = card_reason(self.approval, p.call, p.effect, self.log)
        if self.claim.journal_accepts(APPROVAL_REASONS):
            reason = reason_for_journal(
                offering_edit(p.call, p.effect, reason, self._input_schema(p.call.name)),
                layers=self.claim.journal_accepts(APPROVAL_RULE_LAYERS),
                rule_grants=self.claim.journal_accepts(APPROVAL_RULE_GRANTS),
                edition_features=edition_features(self.claim.journal_accepts),
                edits=self.claim.journal_accepts(APPROVAL_EDITS),
                external_policy=self.claim.journal_accepts(EXTERNAL_POLICY_ASKS),
                edit_order=self.claim.journal_accepts(APPROVAL_EDIT_ORDER),
                args_digest=self.claim.journal_accepts(APPROVAL_ARGS_DIGEST),
            )
        else:
            reason = None
        if reason is None:
            # An older journal refuses the event (UX2): the card shows the call alone and offers no grant, as before UX1.
            self.log.info("tool.approval_reason_withheld", interactionId=ix, reason="journal_lacks_feature")
            return None
        self.log.info("tool.approval_explained", interactionId=ix, toolId=p.effect.tool_id, rule=reason.get("rule"))
        return reason

    async def _hold(self) -> bool:
        """Hold the run's open approvals up to the hold; ``True`` when one was
        decided (or the run was cancelled), so the loop goes on instead of suspending."""
        if self.bridge is None:
            return False
        open_ids = [
            i.interaction_id
            for i in self.run.invocations_for(self.run.turn)
            if i.state == "awaiting_approval" and i.interaction_id is not None
        ]
        if not open_ids or any(is_connect_interaction(ix) for ix in open_ids):
            return False
        started = self.k.clock.suspend_aware()
        binding = RunBinding(self.claim.org_id, self.claim.stream_id, self.claim.run_id)
        self.log.info("tool.approval_hold", interactionIds=open_ids, holdS=self.k.approval_hold_s)
        try:
            # Any id of the batch ends the hold: the loop runs what is decided and holds the rest again.
            woke = await self.bridge.wait_any(
                [InteractionTicket(ix, binding) for ix in open_ids], within_s=self.k.approval_hold_s
            )
        except _DrainRequested:
            # The next worker holds it again; the loop yields this attempt.
            self.log.info("tool.approval_hold_drained", interactionIds=open_ids)
            return True
        waited_ms = round((self.k.clock.suspend_aware() - started) * 1000)
        if woke is None:
            self.log.info("tool.approval_deferred", interactionIds=open_ids, waitedMs=waited_ms)
            return False
        ix, decision = woke
        self.log.info("tool.approval_decided", interactionId=ix, decision=decision, waitedMs=waited_ms)
        return True

    # ---- InteractionView: the run's interactions over its own fold and commits ----

    async def refresh(self) -> None:
        self.lease.check()
        if self.k.draining:
            raise _DrainRequested
        await self._sync()

    def requested(self, interaction_id: str) -> bool:
        return interaction_id in self.run.interactions

    def decision(self, interaction_id: str) -> InteractionDecision | None:
        """A cancel ends every wait: the loop then terminates the run."""
        if self.run.cancel_requested:
            return "cancelled"
        record = self.run.interactions.get(interaction_id)
        if record is None or record.decision is None:
            return None
        return cast("InteractionDecision", record.decision)

    def said(self, interaction_id: str) -> str | None:
        record = self.run.interactions.get(interaction_id)
        return said_in(record.answer_ref) if record is not None else None

    def _approved_edit(self, inv: Invocation) -> ApprovedEdit | None:
        """The arguments a person approved ``inv`` with in place of the model's (AP-06), from the journal, so a
        retry or a new attempt runs what was approved; ``None``: approved as asked, or not approved."""
        record = self.run.interactions.get(inv.interaction_id) if inv.interaction_id else None
        if record is None or record.purpose != "approve" or record.decision != "approved":
            return None
        return approved_edit(record.answer_ref)

    async def append(self, events: Sequence[NewEvent]) -> None:
        _ = await self._commit("ask", list(events))

    async def _suspend(self, asking: list[_Planned]) -> Disposition | None:
        """``None``: Node refused the suspension because nothing is left to wait
        on; the decisions are folded and the loop goes on."""
        events = self._requested_events(asking)
        first: str | None = next(
            (interaction_id(p.inv.invocation_id, _purpose(p.mode)) for p in asking),
            None,
        )
        if first is None:
            first = next(
                (
                    i.interaction_id
                    for i in self.run.invocations.values()
                    if i.interaction_id and i.state in ("awaiting_approval", "unknown")
                ),
                None,
            )
        await afailpoint("worker.batch.before_suspend")
        try:
            _ = await self._commit("suspend", events, Suspend("approval", first))
        except InteractionResolved:
            self.log.info("run.suspend_refused", reason="interaction_resolved", interactionId=first)
            await self._sync()
            if self._open_connects:
                # The owner connected before the suspend landed: the next attempt loads their credential.
                return await self._yield("capability_wait")
            return None
        self.log.info(
            "run.suspend",
            reason="approval",
            interactionId=first,
            asking=sum(1 for p in asking if p.mode == "ask"),
            connecting=sum(1 for p in asking if p.mode == "connect"),
            unknown=sum(1 for p in asking if p.mode == "unknown"),
        )
        return Disposition("suspended", first)

    def _preempted(self) -> bool:
        """The claimer gave this run's slot to a higher class (G-45): it yields here, between steps, never inside a
        model call or a tool effect, and only after this attempt journaled a step, so each claim moves it on."""
        asked = self.k.preempt
        return (
            asked is not None
            and self._commits > 0
            and asked.requested(self.claim.run_id)
            and self.claim.journal_accepts(PRIORITY_PREEMPTION)
        )

    async def _yield(
        self, reason: YieldReason, delay_s: float = 0.0, events: Sequence[NewEvent] = ()
    ) -> Disposition:
        ref: str | None = None
        if self.k.checkpoints is not None:
            ref = await self.k.checkpoints.save(make_checkpoint(self.run, self.session))
        not_before = db_timestamp(self.store_time.now() + timedelta(seconds=delay_s))
        _ = await self._commit("yield", events, Yield(not_before, reason, ref))
        self.log.info(
            "run.yield", reason=reason, notBefore=not_before, checkpoint=ref is not None
        )
        return Disposition("yielded", reason)

    async def _synthetic_outcomes(self, room: int) -> list[NewEvent]:
        """An outcome for each dangling tool_call, so the next provider request is valid; at most
        ``room`` of them, so the commit that ends the run is never too large to be accepted. A call
        left without one reads as cancelled (the transcript pairs it, as for a run Node ends itself),
        so the calls whose effect may have been applied are the ones kept first."""
        dangling = [
            inv for inv in self.run.invocations_for(self.run.turn) if inv.state != "completed"
        ]
        unsure = [inv for inv in dangling if _maybe_applied(inv)]
        kept = {
            inv.invocation_id
            for inv in [*unsure, *(inv for inv in dangling if not _maybe_applied(inv))][: max(0, room)]
        }
        if len(kept) < len(dangling):
            self.log.warning(
                "run.synthetic_outcomes_bounded",
                dangling=len(dangling),
                kept=len(kept),
                effectUnknownOmitted=sum(1 for inv in unsure if inv.invocation_id not in kept),
                maxEvents=self.k.max_commit_events,
            )
        events: list[NewEvent] = []
        for inv in dangling:
            if inv.invocation_id not in kept:
                continue
            if _maybe_applied(inv):
                execution = ToolExecution(
                    status="error", content="run stopped; effect status unknown"
                )
                events.append(
                    await self.effect_log.outcome_event(inv.invocation_id, execution)
                )
            else:
                execution = ToolExecution(status="error", content="cancelled")
                events.append(
                    await self.effect_log.outcome_event(
                        inv.invocation_id, execution, status="cancelled"
                    )
                )
        return events

    async def _unless_cancelled[T](self, work: Awaitable[T], *, streams: bool = False) -> T:
        """``work``, unless the run is cancelled first (UX3, R1 F5): a model call or a
        batch of reads in flight is raced against a cancel, so Stop does not wait for
        it to finish. A cancel ends the run here, keeping the text the call streamed
        before the worker was told of it (R50JFIX-4): ``streams``, a model call, whose
        reply that ends while a cancel check is reading the journal is decided by that
        check, so text that came after the person pressed Stop is never their answer."""
        wakeup = self.k.cancel_wakeup
        if wakeup is None:
            return await work
        check = _CancelCheck()
        task: asyncio.Future[T] = asyncio.ensure_future(work)
        watcher = asyncio.ensure_future(self._cancel_requested(wakeup, check))
        try:
            done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if streams and task in done and not check.found:
                # The journal order decides, not clocks: a check that began before the reply ended answers first.
                await check.idle.wait()
            if task in done and not check.found:
                return task.result()
        finally:
            for pending in (task, watcher):
                if not pending.done():
                    _ = pending.cancel()
            _ = await asyncio.gather(task, watcher, return_exceptions=True)
        self.log.info("run.cancel", reason="cancel_requested", during="call")
        await self._sync()
        raise _Halt(
            await self._terminate(
                Cancelled(), "cancelled", "stopped", answer="".join(self._streamed[: check.kept])
            )
        )

    async def _cancel_requested(self, wakeup: InteractionWakeup, check: _CancelCheck) -> None:
        """Returns once the run's journal holds a cancel it has not folded; a wake (Node
        publishes one with every cancel) cuts the wait, the timeout covers a lost one.
        ``check`` marks what had streamed when each read began: a read that finds the
        cancel keeps only that."""
        while True:
            _ = await wakeup.wait(self.claim.run_id, self.k.cancel_poll_s)
            check.begin(len(self._streamed))
            try:
                # A wakeup that answers without waiting must not starve the call it watches.
                await asyncio.sleep(0)
                try:
                    events = await self.k.journal.read(self.claim.stream_id, after_seq=self.run.seq)
                except Exception as exc:  # a failed read is not a cancel; the next one may tell
                    self.log.debug("run.cancel_check_failed", errorType=type(exc).__name__)
                    continue
                if any(
                    e.type == "run.cancel_requested" and e.run_id == self.claim.run_id
                    for e in events
                ):
                    check.found = True
                    return
            finally:
                check.end()

    async def _terminate(
        self,
        transition: Transition,
        status: DispositionStatus,
        ending: RunEndingOutcome | None = None,
        answer: str | None = None,
    ) -> Disposition:
        reason = getattr(transition, "error_type", None)
        finalized = (
            await self._finalize(ending, reason, answer=answer) if ending is not None else []
        )
        synthetic = await self._synthetic_outcomes(
            self.k.max_commit_events - len(self._riding) - len(finalized)
        )
        events = [*synthetic, CommitUnit(tuple(finalized))]
        try:
            _ = await self._commit(status, events, transition)
        except CommitRejected as refused:
            # A refusal no retry changes must not keep the run from ending: every later claim would
            # be refused the same way. The transition alone is what ends it.
            self.log.warning(
                "run.terminal_events_refused",
                status=status,
                errorType=reason,
                events=len(synthetic) + len(finalized),
                refusal=type(refused).__name__,
            )
            self._riding = []
            synthetic = []
            _ = await self._commit(status, [], transition)
        self.log.info(
            "run.terminate",
            status=status,
            errorType=reason,
            syntheticOutcomes=len(synthetic),
            leftInputs=len(self.run.queued_inputs),
        )
        return Disposition(status, reason)

    async def _fail_safely(self, error_type: str, message: str) -> Disposition:
        try:
            return await self._terminate(
                Fail(error_type, message[:500] or None), "failed"
            )
        except Exception as exc:
            # Nothing could be recorded: leave the run to lease expiry, re-claim and,
            # if it keeps failing, the quarantine cap.
            self.log.error("run.fail_not_journaled", exc=exc, errorType=error_type)
            return Disposition(
                "lost", f"{error_type}; not journaled: {type(exc).__name__}"
            )


class _DrainRequested(Exception):
    """SIGTERM arrived while a call was held for approval."""


class _IncompatibleError(Exception):
    pass


class _CancelCheck:
    """One raced call's cancel checks (R50JFIX-4): ``kept``, how many streamed deltas preceded the read in flight or
    the last one; ``idle`` is set while no read is in flight; ``found``: a read found the cancel."""

    def __init__(self) -> None:
        self.kept = 0
        self.found = False
        self.idle = asyncio.Event()
        self.idle.set()

    def begin(self, streamed: int) -> None:
        self.kept = streamed
        self.idle.clear()

    def end(self) -> None:
        self.idle.set()


class _LiveStream:
    """One sample's deltas, coalesced into numbered live frames.

    The first delta goes out as a frame of its own, so the first token never
    waits out a window. After it, a frame is flushed when its window has passed
    (checked on the next delta and by a timer), when the delta kind changes,
    and when the sample ends, so ``n`` is gap-free per ``(fence, modelAttempt)``.
    """

    def __init__(self, run: _ClaimedRun, model_attempt: int) -> None:
        self._run = run
        self._attempt = model_attempt
        self._kind: DeltaKind | None = None
        self._parts: list[str] = []
        self._since = 0.0
        self._n = 0
        self._timer: asyncio.TimerHandle | None = None
        self._lock = asyncio.Lock()

    async def publish(self, kind: DeltaKind, text: str) -> None:
        if kind == "text_delta" and text:
            self._run.streamed(text)
        if self._run.k.live is None or not text:
            return
        if self._kind is not None and kind != self._kind:
            await self.flush()
        now = self._run.k.clock.suspend_aware()
        if not self._parts:
            self._since = now
            self._arm()
        self._kind = kind
        self._parts.append(text)
        if self._n == 0 or now - self._since >= self._run.k.live_window_s:
            await self.flush()

    def _arm(self) -> None:
        window = self._run.k.live_window_s
        if window <= 0:
            return
        loop = asyncio.get_running_loop()
        self._timer = loop.call_later(window, lambda: loop.create_task(self.flush()))

    async def flush(self) -> None:
        async with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._parts or self._kind is None:
                return
            self._n += 1
            frame = LiveFrame(
                self._run.claim.run_id,
                self._run.claim.fence,
                self._attempt,
                self._n,
                self._kind,
                {"text": "".join(self._parts)},
            )
            self._parts = []
            await self._run.publish_live(frame)


def _maybe_applied(inv: Invocation) -> bool:
    return inv.state in ("intent", "unknown") or (
        inv.state == "approved" and inv.intent_attempt_id is not None
    )


def _merge_committed(report: ChildMergeReport | None) -> list[NewEvent]:
    """The revision a joined child's merge made of this run's workspace (P6.4), so its versions are journaled."""
    if report is None or report.revision is None:
        return []
    kinds: Mapping[str, FileChangeKind]
    if report.merged:
        changed, kinds = report.merged, report.kinds
    else:
        changed = tuple(f"{report.unfinished}/{path}" for path in report.kept)
        # A failed child's work is kept in a folder of its own, new in the parent's workspace.
        added: FileChangeKind = "added"
        kinds = dict.fromkeys(changed, added)
    if not changed:
        return []
    return [workspace_committed(report.into, report.revision, changed, kinds)]


def _terminal_disposition(status: str) -> DispositionStatus:
    if status in ("completed", "failed", "cancelled", "quarantined"):
        return cast("DispositionStatus", status)
    return "failed"


__all__ = [
    "MAX_ATTEMPTS",
    "MAX_EFFECT_RETRIES",
    "Disposition",
    "DispositionStatus",
    "DurableKernel",
]
