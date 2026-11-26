"""``EngineKernel``: one claimed attempt of an engine run, bound to the journal (ARCHITECTURE §4.1, §5.2, §5.4).

Sibling of ``DurableKernel``, registered for ``executor.kind = "engine"``
(``RunDriver(kernels={"engine": EngineKernel(...)})``, F2). What it decides,
it decides from the journal (``fold_engine_run``), so any attempt decides the
same:

* no turn yet: the first turn, with the run's input;
* a turn started and not completed (its attempt died): the same turn again,
  from the last checkpoint and the workspace head, the engine told it was
  interrupted; with no usable state, from the workspace with a summary of the
  journal, flagged;
* a completed turn with queued input or decided approvals: a follow-up turn;
  with undecided approvals: suspend on them; otherwise: complete.

Each turn: the run's tool endpoint is this attempt's (``JournalEffectLog`` over
the fenced commit, approvals held through the run's interactions, so an effect
dedupes by key across attempts); engine progress is journaled in batches with
deterministic command ids (``JournalEngineEventSink``); the turn ends in one
commit holding its checkpoint, its final text and ``turn.completed`` (and the
run's completion when it is the last). A process or sandbox that died mid-turn
releases the claim: the next attempt resumes. A cancel (``run.cancel_requested``)
stops the turn and discards its drafts; a drain or a lost lease interrupts it
and keeps them.

Product-neutral: the host's assembler and presenter are ports; time, ids and
sleeping are injected.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, cast

from pydantic import JsonValue  # noqa: TC002 - a runtime annotation of a dict literal

from harness_core.access.types import AccessRouteRef
from harness_core.artifacts.types import Derivation
from harness_core.budget.run_budget import PersonWaits, RunBudget
from harness_core.budget.types import MICRO_USD
from harness_core.context.user_compact import compacted_event
from harness_core.contracts import canonical_digest, canonical_json
from harness_core.driver.refusal import RunRefused
from harness_core.driver.run_driver import CapabilityLost
from harness_core.effects.edits import offering_edit
from harness_core.effects.flow import (
    EXPLAINED_EVENT,
    SENSITIVITIES,
    GrantingLedger,
    edition_features,
    reason_for_journal,
)
from harness_core.effects.journal import JournalEffectLog, closing_outcomes
from harness_core.effects.results import put_ref
from harness_core.effects.types import MAX_DENY_REASON_CHARS, ToolExecution
from harness_core.engines.allowance import allowance_note
from harness_core.engines.briefing import BriefedFile, earlier_files, engine_task_prompt
from harness_core.engines.controller import (
    BUDGET_STOP_CODES,
    KEPT_LABEL,
    RESUME_NOTE,
    EngineRunContext,
)
from harness_core.engines.durable.carry import SessionEngineRuns, pinned_carry
from harness_core.engines.durable.fold import (
    DEFERRED_SLOT,
    EngineRunState,
    TurnRecord,
    checkpoint_data,
    fold_engine_run,
    resume_summary,
)
from harness_core.engines.durable.handoff import (
    HANDOFF_PATH,
    NOTE_MAX_BYTES,
    NOTES_MAX_BYTES,
    OUTPUTS_DIR,
    PARTIAL_ANSWER_PATH,
    DoneCall,
    close_handoff,
    earlier_partial,
    handoff_deliverable,
    handoff_summary,
    is_engine_note,
    is_open,
    original_request,
    read_handoff,
    save_handoff,
    source_of,
)
from harness_core.engines.durable.inputs import (
    SkippedInput,
    distinct_inputs,
    pin_data,
    resolve_each,
    skip_data,
    trigger_input,
)
from harness_core.engines.durable.ports import (
    AsksQuestions,
    EngineAttempt,
    GatheredResult,
    LinksFiles,
    ShowsTimePlan,
    SilentPresenter,
)
from harness_core.engines.durable.sink import JournalEngineEventSink
from harness_core.engines.durable.tool_pin import PinsToolList
from harness_core.engines.durable.wrap_up import ENGINE_DRAFT, ENGINE_NOTES
from harness_core.engines.environments import EnvironmentInvalid, pinned_environment
from harness_core.engines.errors import (
    EngineGone,
    EngineInputsUnsupported,
    EngineRefusal,
)
from harness_core.engines.events import turn_id_for
from harness_core.engines.native_policy import POLICY_CHECK_TOOL
from harness_core.engines.outcome.briefing import OUTCOME_KEY
from harness_core.engines.outcome.deliverable import (
    ANSWER,
    Deliverable,
    pinned_deliverable,
)
from harness_core.engines.outcome.faults import (
    RunFault,
    engine_stop_cut,
    fault_of,
)
from harness_core.engines.outcome.gathered import gathered_sources
from harness_core.engines.outcome.ledger import (
    OUTCOME_LEDGER,
    PLACEHOLDER_MAX_BYTES,
    ArtifactWritten,
    LimitReached,
    NoteSaved,
    OutcomeLedger,
    SaveRefused,
    SourceGathered,
    StepCompleted,
    ledger_from_events,
    recorded_events,
    workspace_artifacts,
)
from harness_core.engines.outcome.outcome import (
    CLOSING_MAX_CHARS,
    UNFINISHED,
    Delivered,
    Failed,
    outcome_wire,
    settle,
    settled_event,
)
from harness_core.engines.outcome.presenter import PRESENTER, Framed
from harness_core.engines.pins import ImagePins, image_pins
from harness_core.engines.profiles import profile_digest, profile_from_wire
from harness_core.engines.tool_folder import granted_tools
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.failpoints import afailpoint
from harness_core.gates.spec import verify_gate
from harness_core.gates.verify import (
    CheckResult,
    RunVerifier,
    StoredWorkspaceFiles,
    Verification,
    VerifyPorts,
)
from harness_core.gateway.asking import AskedQuestion, parse_question
from harness_core.gateway.disclosure import CALL_TOOL, unwrap_call
from harness_core.gateway.types import McpResult
from harness_core.interactions.connect import skipped_connects
from harness_core.interactions.connect_wait import BridgeConnectWait
from harness_core.interactions.hold import APPROVAL_HOLD_S, BridgeApprovalHold
from harness_core.interactions.journal import POLL_S, JournalInteractionBridge
from harness_core.interactions.memory import NoWakeup
from harness_core.interactions.ports import RunBinding
from harness_core.interactions.run_view import ClaimInteractionView
from harness_core.journal.bounds import (
    ANSWER_MAX_BYTES,
    CHILD_RESULT_MAX_BYTES,
    bounded_child_document,
    bounded_text,
)
from harness_core.journal.children import child_result_document, child_workspace
from harness_core.journal.commit_parts import pack
from harness_core.journal.ports import SealingContentStore
from harness_core.journal.rejected import COMMIT_REJECTED, sealed_completion
from harness_core.journal.types import (
    APPROVAL_ARGS_DIGEST,
    APPROVAL_EDIT_ORDER,
    APPROVAL_EDITS,
    APPROVAL_REASONS,
    APPROVAL_RULE_LAYERS,
    CONNECT_PAUSES,
    CONTEXT_COMPACTED,
    CONTEXT_COMPACTED_AUTO,
    EXTERNAL_POLICY_ASKS,
    INTENT_ALLOWED_BY,
    NATIVE_EVENTS,
    OUTCOME_WORDS,
    PRIORITY_PREEMPTION,
    SKIPPED_INPUTS,
    Cancelled,
    ChildResultRef,
    CommitRejected,
    CommitRequest,
    Complete,
    ContentRef,
    Fail,
    InputPending,
    InteractionResolved,
    JournalUnavailable,
    NewEvent,
    OwnershipLost,
    Quarantine,
    Suspend,
    Yield,
    YieldReason,
    command_id,
    db_timestamp,
)
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.durable.capability import (
    CAPABILITY_LOST,
    CAPABILITY_WAIT,
    SANDBOX_RECHECK_S,
    SANDBOX_UNAVAILABLE,
    SANDBOX_WAIT,
    STORE_RECHECK_S,
    STORE_UNAVAILABLE,
    STORE_WAIT,
    TOOL_WITHDRAWN,
    capability_changes,
    capability_waits,
    recheck_delay,
    returned,
    withdrawn_servers,
)
from harness_core.kernel.durable.driver import MAX_ATTEMPTS, Disposition
from harness_core.kernel.durable.lease import (
    JournalGone,
    Lease,
    LeaseKeeper,
    LeaseLost,
    while_leased,
)
from harness_core.kernel.durable.provider_wait import ProviderWaits
from harness_core.kernel.durable.slots import CAPABILITY_SLOT
from harness_core.kernel.durable.transcript import decode_assistant, encode_assistant
from harness_core.kernel.fold import RunState
from harness_core.logs import HarnessLog
from harness_core.sandbox.errors import (
    SandboxOutOfMemory,
    SandboxRefusal,
)
from harness_core.telemetry import genai
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.ports import RunRef
from harness_core.workspace.forks import session_scope, session_workspace_id
from harness_core.workspace.sync.storage_classes import (
    STORAGE_RECORD_PATH,
    Reference,
    decode_record,
)
from harness_core.workspace.types import (
    ObjectScope,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence

    from harness_core.budget.types import Remaining
    from harness_core.clock import Clock
    from harness_core.effects.edits import ApprovedEdit
    from harness_core.effects.flow import Sensitivity
    from harness_core.effects.runner import HoldDecision
    from harness_core.effects.types import EffectCall
    from harness_core.engines.controller import EngineTurnOutcome
    from harness_core.engines.durable.ports import (
        AssembledEngineRun,
        EngineRunPresenter,
        EngineRuntimeAssembler,
    )
    from harness_core.engines.events import EngineStateBlob
    from harness_core.engines.outcome.outcome import PartialReason, RunOutcome
    from harness_core.engines.ports import EngineSuccessors, SourceLedger
    from harness_core.engines.types import EngineInput, EngineProfile, EngineVersion
    from harness_core.gateway.ports import Progress, ToolEndpoint
    from harness_core.gateway.types import GatewayClaims, McpTool
    from harness_core.interactions.ports import InteractionDecision, InteractionWakeup
    from harness_core.journal.ports import ContentStorePort, JournalPort
    from harness_core.journal.types import (
        Claim,
        CommitReceipt,
        JournalEvent,
        Transition,
    )
    from harness_core.kernel.core.turn_limit import CutShort
    from harness_core.kernel.durable.driver import DispositionStatus
    from harness_core.priority import PreemptRequests
    from harness_core.sandbox.ports import SandboxBroker
    from harness_core.telemetry.ports import HarnessTracer
    from harness_core.workspace.ports import WorkspaceStore
    from harness_core.workspace.types import ObjectRef, WorkspaceFile, WorkspaceManifest

LOGGER = "harness_core.engines.durable"
ENGINE = "engine"
# The worker feature a worker advertises when it runs this kernel; Node routes engine runs only to it.
EXECUTOR_ENGINE: Final = "executor_engine"
# Advertised beside it when the kernel resolves a run's input artifacts (E1b): Node routes a run naming some
# only to such a worker, never to one that would run without them.
ENGINE_INPUTS: Final = "engine_inputs"
# WP-70: a normal turn end that still owes the requested file, as the wrap-up words it.
UNFINISHED_END = cast("CutShort", UNFINISHED)
ENGINE_PROFILE_MISSING = "engine_profile_missing"
ENGINE_PROFILE_MISMATCH = "engine_profile_mismatch"
ENGINE_FAILED = "engine_failed"
BUDGET_EXHAUSTED = "budget_exhausted"
# Which limit an engine turn's ``outcome.limit`` cut short (run.completed.truncatedBy, BUDGETEND).
# The Model Gateway's codes for a provider failure that waiting can fix (``classify_provider_failure``): a 5xx, a
# dropped connection or a stream the provider failed, and a rate limit the engine's own retry did not outlast. One
# child failed on two such calls a second apart; it now waits them out as the loop does (P4.9), its slot
# given back (``llm_backoff``), on the loop's bound (``provider_wait``), then ends cut short by the provider with what
# it gathered.
TRANSIENT_PROVIDER_CODES: frozenset[str] = frozenset({"provider_unavailable", "rate_limited"})
LLM_BACKOFF: YieldReason = "llm_backoff"


# WP-84: an engine that ends its turn on an error of its own is cut short by it (``faults.engine_stop_cut``).
ENGINE_ERROR_CUT: CutShort = "engine_error"
# WP-103: a model call PipesHub blocked as carrying a secret cuts the run short by that reason, failing with this one
# code when nothing was gathered (``faults.engine_stop_cut``).
SECRET_BLOCKED_CUT: CutShort = "secret_blocked"
_CUT_SHORT_BY_LIMIT: dict[str, CutShort] = {
    "budget": "budget", "tokens": "budget", "steps": "turns", "wall_time": "time", "stall": "stall",
}
CANCEL_REQUESTED = "cancel_requested"
FLUSH_INTERVAL_S = 1.0
INTERRUPTED_NOTE = (
    "Your previous attempt at this step was interrupted before it finished. The workspace holds the files it "
    "saved. Continue from where it stopped; do not redo work that is already in the workspace."
)
# EV2-24: a re-driven turn starts from the workspace its interrupted attempt saved; naming what it saved keeps
# the engine from applying an edit a second time (the restored state predates the turn's saves).
SAVED_NOTE = (
    "Files it saved during this step, with its changes already applied (read a file before you change it, "
    "and do not apply the same change to it again): "
)
# ADR-RUN-06 D4: a later turn's allowance and time plan, which replace what the session was told before.
RENEWED_ALLOWANCE = "This turn has a fresh allowance, which replaces any time plan you were given earlier. "
_ENGINE_PROVIDER = "engine"
_RECOVERED = "interrupted before its result was recorded; you may call it again"


@dataclass(frozen=True)
class _Drive:
    index: int
    prompt: str
    redrive: bool = False
    consume: tuple[int, ...] = ()
    injected: bool = False


@dataclass(frozen=True)
class _Wait:
    interaction_id: str


@dataclass(frozen=True)
class _Finish:
    turn: TurnRecord


class _Halt(Exception):
    def __init__(self, disposition: Disposition) -> None:
        super().__init__(disposition.status)
        self.disposition = disposition


@dataclass
class _Ids:
    """Invocation ids unique to the attempt: a later attempt never reuses one."""

    attempt_id: str
    n: int = 0

    def new_id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.attempt_id}_{self.n}"[:128]


def unwrapped_arguments(name: str, arguments: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """The arguments of the tool a call ran: through ``call_tool``, the wrapped tool's."""
    if name == CALL_TOOL:
        unwrapped = unwrap_call(arguments)
        if isinstance(unwrapped, tuple):
            return unwrapped[1]
    return arguments


class _TrackingEndpoint:
    """The run's endpoint as this attempt serves it: progress is journaled before each call, and a call the
    endpoint deferred for approval is remembered so the turn suspends on it; whether the engine ever listed
    its tools (P9 FM-6: an engine that could not reach them must not complete the turn without them)."""

    def __init__(
        self,
        inner: ToolEndpoint,
        before: Callable[[], Awaitable[None]],
        after: Callable[[str, Mapping[str, JsonValue], McpResult], Awaitable[None]] | None = None,
        calls: dict[str, tuple[str, Mapping[str, JsonValue]]] | None = None,
        asked: Callable[[AskedQuestion], Awaitable[None]] | None = None,
        egress_flow: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._inner = inner
        self._before = before
        self._after = after
        self._on_asked = asked
        self._egress_flow = egress_flow
        self.deferred: list[str] = []
        # ADR-RUN-06 D2: the deferred calls whose decision already reached this turn, and a decisions note waiting
        # to ride on the next result for an engine that cannot be steered.
        self.relayed: set[str] = set()
        self._carried: tuple[str, frozenset[str]] | None = None
        # The questions the engine asked the person this turn (``ask_user``): the turn then ends the run needs_input.
        self.asked: list[AskedQuestion] = []
        self.listed = False
        # By invocation: the tool and arguments the engine sent, for the handoff of a run a limit cuts short.
        self.calls = calls if calls is not None else {}

    async def list_tools(self, claims: GatewayClaims) -> tuple[McpTool, ...]:
        tools = await self._inner.list_tools(claims)
        self.listed = True
        return tools

    def granted_tools(self) -> tuple[McpTool, ...]:
        return granted_tools(self._inner)

    async def call_tool(
        self,
        claims: GatewayClaims,
        name: str,
        arguments: Mapping[str, JsonValue],
        *,
        progress: Progress | None = None,
    ) -> McpResult:
        # The managed hook's policy check (G-25) is no call of the run's: no progress line, no activity.
        if name == POLICY_CHECK_TOOL:
            return await self._inner.call_tool(claims, name, arguments, progress=progress)
        await self._before()
        result = await self._inner.call_tool(claims, name, arguments, progress=progress)
        result = await self._narrowed(result)
        if result.disposition == "deferred" and result.interaction_id and result.interaction_id not in self.deferred:
            self.deferred.append(result.interaction_id)
        elif result.disposition == "asked":
            asked = parse_question(unwrapped_arguments(name, arguments))
            if isinstance(asked, AskedQuestion) and asked not in self.asked:
                self.asked.append(asked)
                if self._on_asked is not None:
                    await self._on_asked(asked)
        elif self._after is not None:
            await self._after(name, arguments, result)
        if result.invocation_id is not None:
            self.calls[result.invocation_id] = (name, dict(arguments))
        return self._with_carried(result)

    def waiting(self) -> list[str]:
        """The deferred calls whose decision has not reached the turn yet (relayed or riding on a result)."""
        carried = self._carried[1] if self._carried is not None else frozenset()
        return [ix for ix in self.deferred if ix not in self.relayed and ix not in carried]

    def unrelayed(self) -> list[str]:
        """What the turn still owes the engine at its end: a decision the next turn tells it, or a wait to suspend on."""
        return [ix for ix in self.deferred if ix not in self.relayed]

    def carry(self, note: str, interactions: Iterable[str]) -> None:
        """``note`` opens the next result the engine is handed (an engine that cannot be steered mid-turn)."""
        held = self._carried
        if held is None:
            self._carried = (note, frozenset(interactions))
        else:
            self._carried = (f"{held[0]}\n{note}", held[1] | frozenset(interactions))

    def _with_carried(self, result: McpResult) -> McpResult:
        held = self._carried
        if held is None:
            return result
        self._carried = None
        self.relayed.update(held[1])
        return replace(result, text=f"[{held[0]}]\n{result.text}")


    async def _narrowed(self, result: McpResult) -> McpResult:
        """SEC-A-02: the sandbox's egress narrowed to what this result brought into the conversation before the engine
        reads it; when the proxy could not be told, the result is withheld, never handed to a sandbox that could still
        send it out."""
        if self._egress_flow is None:
            return result
        try:
            await self._egress_flow()
        except Exception:
            return McpResult(
                EGRESS_FLOW_WITHHELD, is_error=True, disposition="refused", retryable=True,
                invocation_id=result.invocation_id, reason_code=EGRESS_FLOW_UNENFORCED,
            )
        return result


# SEC-A-02: what the engine reads when a call's result is withheld because its sandbox's egress could not be narrowed.
EGRESS_FLOW_UNENFORCED: Final = "egress_rebind_failed"
EGRESS_FLOW_WITHHELD: Final = (
    "PipesHub withheld this result: it holds private data, and the sandbox's network could not be limited to match "
    "in time. Try the call again; if it fails again, tell the user. [gateway:egress_rebind_failed]"
)


class _PresentingView:
    """The run's interactions as the approval bridge sees them; a request is journaled with its approval line,
    so the approval card has the call it is about (P2.6c FB-7), and with why the policy asks (UX2), when the
    journal accepts that (an older one would refuse the commit: the card then shows the call alone)."""

    def __init__(
        self,
        inner: ClaimInteractionView,
        sink: JournalEngineEventSink,
        *,
        explains: bool,
        layers: bool,
        log: HarnessLog,
        edition_features: frozenset[str] = frozenset(),
        edits: bool = False,
        external_policy: bool = False,
        edit_order: bool = False,
        args_digest: bool = False,
    ) -> None:
        self._inner = inner
        self._sink = sink
        self._explains = explains
        self._layers = layers
        self._edition_features = edition_features
        self._edits = edits
        self._external_policy = external_policy
        self._edit_order = edit_order
        self._args_digest = args_digest
        self._log = log
        self.asking: dict[str, tuple[str, Mapping[str, JsonValue]]] = {}
        self.reasons: dict[str, Mapping[str, JsonValue]] = {}

    async def refresh(self) -> None:
        await self._inner.refresh()

    def requested(self, interaction_id: str) -> bool:
        return self._inner.requested(interaction_id)

    def decision(self, interaction_id: str) -> InteractionDecision | None:
        return self._inner.decision(interaction_id)

    def said(self, interaction_id: str) -> str | None:
        return self._inner.said(interaction_id)

    def edit(self, interaction_id: str) -> ApprovedEdit | None:
        return self._inner.edit(interaction_id)

    async def append(self, events: Sequence[NewEvent]) -> None:
        shown: list[NewEvent] = []
        for event in events:
            ix = str(event.data.get("interactionId", ""))
            if event.type != "interaction.requested":
                continue
            if ix in self.asking and self._sink.turn is not None:
                tool, arguments = self.asking[ix]
                shown.extend(self._sink.presenter.approval(self._sink.stamp(), ix, tool, arguments))
            shown.extend(self._explained(ix))
        await self._inner.append([*shown, *events])

    def _explained(self, ix: str) -> list[NewEvent]:
        reason = self.reasons.get(ix)
        if reason is None:
            return []
        reason = (
            reason_for_journal(
                reason,
                layers=self._layers,
                edition_features=self._edition_features,
                edits=self._edits,
                external_policy=self._external_policy,
                edit_order=self._edit_order,
                args_digest=self._args_digest,
            )
            if self._explains
            else None
        )
        if reason is None:
            self._log.info("tool.approval_reason_withheld", interactionId=ix, reason="journal_lacks_feature")
            return []
        self._log.info("tool.approval_explained", interactionId=ix, toolId=str(reason.get("toolId")), rule=str(reason.get("rule")))
        return [NewEvent(EXPLAINED_EVENT, {"interactionId": ix, **reason}, critical=False)]


class _PresentingHold:
    """``BridgeApprovalHold`` that names the call to the view before the request is journaled."""

    def __init__(self, inner: BridgeApprovalHold, view: _PresentingView) -> None:
        self._inner = inner
        self._view = view
        # The tools the engine is served, set once the attempt is assembled: their schemas order the edit card.
        self.served: PinsToolList | None = None
        # The attempt's person waits (D4), paused while a decision is held here.
        self.waits: PersonWaits | None = None
        # FLOWGRANT: reads the run's stream and says whether a grant now answers the ask; unset, every ask is asked.
        self.granted: Callable[[Mapping[str, JsonValue]], Awaitable[bool]] | None = None

    def interaction_for(self, call: EffectCall) -> str:
        return self._inner.interaction_for(call)

    def _input_schema(self, name: str) -> Mapping[str, JsonValue] | None:
        if self.served is None:
            return None
        return next((t.input_schema for t in self.served.served_tools() if t.name == name), None)

    def explain(self, call: EffectCall, reason: Mapping[str, JsonValue]) -> None:
        self._view.reasons[self._inner.interaction_for(call)] = offering_edit(
            call.call, call.effect, reason, self._input_schema(call.call.name)
        )

    async def hold(self, call: EffectCall) -> HoldDecision | None:
        ix = self._inner.interaction_for(call)
        reason = self._view.reasons.get(ix)
        # FLOWGRANT: a person may have allowed this place for the conversation in another of its runs since this run
        # last read its stream (Node writes the grant on every open run); then nobody is asked again.
        if reason is not None and self.granted is not None and await self.granted(reason):
            _ = self._view.reasons.pop(ix, None)
            return "approved"
        self._view.asking[ix] = (call.call.name, dict(call.call.arguments))
        if self.waits is None:
            return await self._inner.hold(call)
        # ADR-RUN-01 D4: the person decides now; the turn's working time waits with them.
        async with self.waits.waiting():
            return await self._inner.hold(call)

    def denial_reason(self, call: EffectCall) -> str | None:
        return self._inner.denial_reason(call)

    def approved_edit(self, call: EffectCall) -> ApprovedEdit | None:
        return self._inner.approved_edit(call)


_FAILED_DISPOSITIONS = frozenset({"failed", "quarantined", "lost"})
# The engine ran without its granted tools: the first attempt releases with it (a later attempt, bound afresh, drives
# the turn again); a later one fails the run with it, which the chat words. Never a crash loop: its reap would end a
# person's kept sign-in for a PipesHub-side failure (SIGNFIX).
ENGINE_TOOLS_UNREACHABLE = "engine_tools_unreachable"


def _result_text(raw: bytes) -> str:
    """A journaled tool result's text: its ``content`` when it is the effect runner's document, else the bytes."""
    decoded = raw.decode("utf-8", errors="replace")
    try:
        document = json.loads(decoded)
    except ValueError:
        return decoded.strip()
    if isinstance(document, dict) and "content" in document:
        content = cast("dict[str, JsonValue]", document)["content"]
        return (content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)).strip()
    return decoded.strip()


def _engine_kind(claim: Claim) -> str:
    engine = getattr(claim.spec.executor, "engine", None)
    return str(getattr(engine, "kind", None) or "engine")


@dataclass
class EngineKernel:
    journal: JournalPort
    assembler: EngineRuntimeAssembler
    clock: Clock
    # Reaps the sandboxes an earlier attempt of the run left (a dead worker's).
    broker: SandboxBroker | None = None
    # How progress is shown when the assembler names no presenter of its own.
    presenter: EngineRunPresenter = field(default_factory=SilentPresenter)
    # Set: approvals are held in the running run (P2.6c); unset, the journal is polled.
    interactions: InteractionWakeup | None = None
    approval_hold_s: float = APPROVAL_HOLD_S
    poll_s: float = POLL_S
    flush_interval_s: float = FLUSH_INTERVAL_S
    lease_margin_s: float = 2.0
    renew_interval_s: float | None = None
    max_attempts: int = MAX_ATTEMPTS
    journal_retry_s: float = 0.2
    # P9: one ``invoke_agent`` span per attempt (the sandbox acquire inside it is its child).
    tracer: HarnessTracer = field(default_factory=lambda: NOOP_TRACER)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # G-45: the attempts the claimer asked to give their slot to a higher class; unset, never preempted.
    preempt: PreemptRequests | None = None
    # ADR-OPS-01 D6: where a run pinned to a retired version continues (the release's declared successors); unset,
    # such a run is refused as before.
    successors: EngineSuccessors | None = None
    _drain: bool = field(default=False, init=False)
    _running: dict[int, _EngineAttemptRun] = field(default_factory=dict[int, "_EngineAttemptRun"], init=False)

    def request_drain(self) -> None:
        self._drain = True
        for run in list(self._running.values()):
            run.drain()

    @property
    def draining(self) -> bool:
        return self._drain

    def attempt_of(self, claim: Claim, profile: EngineProfile) -> EngineAttempt:
        """What this kernel hands its assembler for ``claim`` (assembler contract suites build from it)."""
        return _EngineAttemptRun(self, claim, self.clock.suspend_aware()).attempt(profile)

    async def run_claimed(self, claim: Claim, *, claim_sent_at: float) -> Disposition:
        run = _EngineAttemptRun(self, claim, claim_sent_at)
        self._running[id(run)] = run
        engine = _engine_kind(claim)
        try:
            with self.tracer.span(
                f"{genai.INVOKE_AGENT} {engine}",
                kind="internal",
                run=RunRef(claim.org_id, claim.run_id, claim.session_id, claim.attempt_id, claim.fence),
                attributes={
                    genai.OPERATION: genai.INVOKE_AGENT,
                    genai.AGENT_NAME: engine,
                    genai.CONVERSATION_ID: claim.session_id,
                },
            ) as span:
                disposition = await run.execute()
                span.set({"pipeshub.disposition": disposition.status, "pipeshub.reason": disposition.reason})
                if disposition.status in _FAILED_DISPOSITIONS:
                    span.end(error_type=disposition.reason or disposition.status)
                return disposition
        finally:
            _ = self._running.pop(id(run), None)


class _LeasedContent:
    """The attempt's content port, retried through a transient outage while the lease holds (as the
    loop's). Bound once the runtime is assembled; nothing is read or written before that."""

    def __init__(self, run: _EngineAttemptRun) -> None:
        self._run = run
        self.inner: ContentStorePort | None = None

    def _store(self) -> ContentStorePort:
        if self.inner is None:
            raise RuntimeError("the attempt's content store is bound when its runtime is assembled")
        return self.inner

    async def put(self, data: bytes) -> ContentRef:
        store = self._store()
        return await self._run.leased(lambda: store.put(data), "content.put")

    async def get(self, ref: ContentRef) -> bytes:
        store = self._store()
        return await self._run.leased(lambda: store.get(ref), "content.get")

    async def put_sealed(self, data: bytes) -> ContentRef | None:
        """``data`` kept out of its ref; ``None`` when the store has one way of keeping content only."""
        store = self._store()
        if not isinstance(store, SealingContentStore):
            return None
        return await self._run.leased(lambda: store.put_sealed(data), "content.put")


class _EngineAttemptRun:
    def __init__(self, kernel: EngineKernel, claim: Claim, sent_at: float) -> None:
        self.k = kernel
        self.claim = claim
        self.lease = Lease(kernel.clock, sent_at=sent_at, lease_ms=claim.lease_ms, margin_s=kernel.lease_margin_s)
        self.store_time = claim.store_time(kernel.clock, sent_at=sent_at)
        self.log = HarnessLog.for_claim(LOGGER, claim)

        self.state = EngineRunState(run_id=claim.run_id)
        self.session_runs = SessionEngineRuns()
        self.ledger: SourceLedger | None = None
        # What the host journals when the ledger shows the run's conversation holding more (OWD-15).
        self._level_events: Callable[[], Sequence[NewEvent]] | None = None
        self.events: list[JournalEvent] = []
        self.binding = RunBinding(claim.org_id, claim.stream_id, claim.run_id)
        # ADR-RUN-01 D4: what this attempt waited for a person; each turn's working time is paused for it.
        self.waits = PersonWaits(kernel.clock.monotonic)
        # ADR-OPS-01 D6: the retired version the pinned profile was moved from, when it resumed on a successor.
        self.resumed_from: EngineVersion | None = None
        # The run's files belong to its conversation, as the controller publishes them (``EngineRunContext.binding``):
        # a child's stream is its own, so drafts settled or a handoff published under the stream were another scope's
        # (WP-70: a cut-short child's report was published beside its placeholder and the placeholder never settled).
        self.artifacts_binding = RunBinding(claim.org_id, claim.session_id, claim.run_id)
        self._lock = asyncio.Lock()
        self._commits = 0
        self._draining = kernel.draining
        self._turns_driven = 0
        self._interrupted: str | None = None
        self._child_result: Callable[[dict[str, JsonValue], Sequence[JournalEvent]], dict[str, JsonValue]] | None = None
        # ADR-RUN-01 D1: what the run owes, resolved once per attempt (``_deliverable``).
        self._pinned: Deliverable | None = None
        self._verify_ports: VerifyPorts | None = None
        self._calls: dict[str, tuple[str, Mapping[str, JsonValue]]] = {}
        self.sink = JournalEngineEventSink(
            run_id=claim.run_id,
            task_id=claim.run_id,
            presenter=kernel.presenter,
            commit=lambda cid, events: self.commit("progress", events, command=cid),
        )
        self.content = _LeasedContent(self)
        if claim.journal_accepts(NATIVE_EVENTS):
            self.sink.seal = self._seal
        self.effect_log = JournalEffectLog(
            self._effect_commit,
            self.content,
            self._effect_state,
            allowed_by=claim.journal_accepts(INTENT_ALLOWED_BY),
        )

    async def _seal(self, data: bytes) -> ContentRef:
        """What an engine sent natively, kept out of its ref (C2.39, C2.40): never inline in the journal."""
        return await self.content.put_sealed(data) or await self.content.put(data)

    def drain(self) -> None:
        self._draining = True

    # ---- lifecycle ----

    async def execute(self) -> Disposition:
        keeper: asyncio.Task[None] | None = None
        try:
            # Renewal runs from the claim on: reaping a dead attempt's sandbox can outlast a lease (G4).
            if self.k.renew_interval_s is not None:
                keeper = asyncio.create_task(
                    LeaseKeeper(
                        self.k.journal, self.claim.token, self.lease, self.k.clock,
                        interval_s=self.k.renew_interval_s, sleep=self.k.sleep,
                    ).run()
                )
            await self.sync()
            if self.state.terminal:
                return Disposition(_terminal(self.state.status), self.state.outcome)
            if self.claim.attempt > self.k.max_attempts:
                self.log.warning(
                    "engine.kernel.quarantine", reason="crash_loop", attempt=self.claim.attempt,
                    maxAttempts=self.k.max_attempts,
                )
                await self._reap({"orgId": self.claim.org_id, "runId": self.claim.run_id})
                # A signed-in sandbox kept for the conversation carries no run id (SIGNIN-5); a crash loop ends it too.
                await self._reap({"orgId": self.claim.org_id, "sessionId": self.claim.session_id, KEPT_LABEL: "1"})
                return await self.terminate(
                    Quarantine(attempts=self.claim.attempt, last_error_type="crash_loop"), "quarantined"
                )
            profile = self._profile()
            if isinstance(profile, str):
                self.log.warning("engine.kernel.refused", reasonCode=profile)
                return await self.terminate(Fail(profile), "failed")
            await self._reap({"orgId": self.claim.org_id, "runId": self.claim.run_id})
            if self.state.cancel_requested:
                return await self.terminate(Cancelled(), "cancelled")
            runtime = await self._assemble(profile)
            try:
                withdrawn = await self._verify_capabilities(runtime)
                if withdrawn is not None:
                    return withdrawn
                return await self._loop(runtime, profile)
            finally:
                if runtime.close is not None:
                    try:
                        await asyncio.shield(runtime.close())
                    except Exception as exc:
                        self.log.error("engine.kernel.close_failed", exc=exc, errorType=type(exc).__name__)
        except _Halt as halt:
            return halt.disposition
        except JournalGone:
            return await self.release("journal_unavailable")
        except (LeaseLost, OwnershipLost) as exc:
            self.log.warning("engine.kernel.lease_lost", reason=type(exc).__name__)
            return Disposition("lost", str(exc))
        except CommitRejected as refused:
            return await self._commit_rejected(refused)
        except Exception as exc:
            outside = fault_of(exc)
            if outside is not None and outside.kind == "store_unavailable":
                # Outside a turn too (a restore before it, a save after it): an outage, never a crash (CH-09).
                return await self._store_wait(exc)
            self.log.error("engine.kernel.error", exc=exc, errorType=type(exc).__name__)
            return await self.release("engine_kernel_error")
        finally:
            if keeper is not None:
                _ = keeper.cancel()

    def _profile(self) -> EngineProfile | str:
        """The pinned profile (E4a), checked against its digest; a refusal code when it cannot be used."""
        engine = getattr(self.claim.spec.executor, "engine", None)
        pinned = getattr(engine, "profile", None)
        if engine is None or pinned is None:
            return ENGINE_PROFILE_MISSING
        route = self.claim.spec.model.policy[0]
        access = AccessRouteRef(
            f"{route.provider}:{route.model}", canonical_digest(route.model_dump(mode="json", by_alias=True))
        )
        try:
            profile = profile_from_wire(pinned, access=access)
        except EngineRefusal as refused:
            return refused.reason_code
        # The digest Node pinned with the profile (E4a): a profile edited after admission never runs.
        if profile_digest(profile) != engine.profile_digest:
            return ENGINE_PROFILE_MISMATCH
        return self._resumed(profile)

    def _resumed(self, profile: EngineProfile) -> EngineProfile:
        """ADR-OPS-01 D6: a run pinned to a version the release retired, which a successor declares it continues,
        runs on that successor (its version and binary); everything else of the pinned profile stands."""
        successor = self.k.successors.resumed_on(profile.engine) if self.k.successors is not None else None
        if successor is None:
            return profile
        self.resumed_from = profile.engine
        self.log.info(
            "engine.kernel.resumed_on_successor", engineKind=profile.engine.kind,
            engineVersion=profile.engine.version, successor=successor.version,
        )
        return replace(profile, engine=successor)

    async def _assemble(self, profile: EngineProfile) -> AssembledEngineRun:
        attempt = self.attempt(profile)
        try:
            runtime = await self.k.assembler.build(attempt)
        except (RunRefused, EngineRefusal, SandboxRefusal) as refused:
            code = _code(refused)
            self.log.warning("engine.kernel.refused", reasonCode=code)
            raise _Halt(await self.terminate(Fail(code, str(refused)[:500]), "failed")) from None
        except CapabilityLost as lost:
            raise _Halt(await self._capability_lost(lost)) from None
        self.content.inner = runtime.content
        if isinstance(attempt.hold, _PresentingHold) and isinstance(runtime.tools, PinsToolList):
            attempt.hold.served = runtime.tools
        session: list[JournalEvent] | None = None
        if runtime.ledger is not None or runtime.tool_pin is not None:
            session = await self.leased(lambda: self.k.journal.read(self.claim.stream_id, after_seq=0), "journal.read")
        if runtime.ledger is not None and session is not None:
            # The session's whole stream: what earlier runs read may already be in the workspace.
            runtime.ledger.apply(session)
            self.ledger = runtime.ledger
            self._level_events = runtime.level_events
            await self._record_level()
        if runtime.tool_pin is not None and session is not None and isinstance(runtime.tools, PinsToolList):
            runtime.tool_pin.apply(session)
            pinned = await runtime.tool_pin.pin(runtime.tools, self.content.put, self.content.get)
            if pinned:
                _ = await self.commit("tools", list(pinned))
        if runtime.presenter is not None:
            # The host's presentation for this attempt (it reads the attempt's stores for diffs).
            self.sink.presenter = runtime.presenter
        if runtime.workspaces is not None:
            self.sink.parent_files = parent_reader(runtime.workspaces, self.claim.org_id, self.log)
        return runtime

    def attempt(self, profile: EngineProfile) -> EngineAttempt:
        """The kernel's half of this attempt, bound to its fenced commit and the run's interactions."""
        view = _PresentingView(
            ClaimInteractionView(self.k.journal, self.claim),
            self.sink,
            explains=self.claim.journal_accepts(APPROVAL_REASONS),
            layers=self.claim.journal_accepts(APPROVAL_RULE_LAYERS),
            log=self.log,
            edition_features=edition_features(self.claim.journal_accepts),
            edits=self.claim.journal_accepts(APPROVAL_EDITS),
            external_policy=self.claim.journal_accepts(EXTERNAL_POLICY_ASKS),
            edit_order=self.claim.journal_accepts(APPROVAL_EDIT_ORDER),
            args_digest=self.claim.journal_accepts(APPROVAL_ARGS_DIGEST),
        )
        bridge = JournalInteractionBridge(
            view, self.k.interactions or NoWakeup(), self.k.clock, self.k.poll_s
        )
        hold = _PresentingHold(BridgeApprovalHold(bridge, self.binding, self.k.approval_hold_s), view)
        hold.waits = self.waits
        hold.granted = self._granted_since
        return EngineAttempt(
            claim=self.claim,
            profile=profile,
            effect_log=self.effect_log,
            hold=hold,  # type: ignore[arg-type]
            guard=self.lease.check,
            propagate=(LeaseLost, OwnershipLost),
            append=lambda events: self._append(events),
            run_events=self._run_events,
            stamp=self.sink.stamp,
            ids=_Ids(self.claim.attempt_id),
            sink=self.sink,
            run_state=self._effect_state,
            connect_wait=(
                lambda gate: BridgeConnectWait(
                    bridge,
                    self.binding,
                    gate,
                    # One connect card per item per task (WP-64): the tree's skips as the claim names them (the
                    # parent's, a sibling's), and this run's own.
                    skipped=set(self.claim.skipped_connects)
                    | skipped_connects(tuple(self.events), self.claim.run_id),
                )
            )
            if self.claim.journal_accepts(CONNECT_PAUSES)
            else None,
        )

    async def _append(self, events: Sequence[NewEvent]) -> None:
        _ = await self.commit("publication", list(events))

    async def _run_events(self) -> Sequence[JournalEvent]:
        await self.sync()
        return tuple(self.events)

    def _effect_state(self) -> RunState:
        return RunState(run_id=self.claim.run_id, invocations=self.state.invocations)

    # ---- the loop ----

    async def _loop(self, runtime: AssembledEngineRun, profile: EngineProfile) -> Disposition:
        self._child_result = runtime.child_result
        self._verify_ports = self._verify_with_workspace(runtime)
        try:
            return await self._turns(runtime, profile)
        finally:
            if runtime.wrap_up is not None:
                # Held for this attempt only: a later attempt holds its own.
                await asyncio.shield(runtime.wrap_up.release())

    async def _turns(self, runtime: AssembledEngineRun, profile: EngineProfile) -> Disposition:
        await self._settle_dead_effects(runtime)
        while True:
            await self.sync()
            state = self.state
            if state.terminal:
                return Disposition(_terminal(state.status), state.outcome)
            if state.cancel_requested:
                return await self._cancelled(runtime)
            if self._draining:
                return await self._yield("drain")
            step = await self._next(state)
            match step:
                case _Wait(interaction_id=ix):
                    suspended = await self._suspend_approval(ix)
                    if suspended is not None:
                        return suspended
                case _Finish(turn=turn):
                    done = await self._complete_after(turn)
                    if done is not None:
                        return done
                case _Drive():
                    if self._preempted():
                        return await self._yield("preempted")
                    done = await self._turn(runtime, profile, step)
                    if done is not None:
                        return done
                    self._turns_driven += 1

    async def _next(self, state: EngineRunState) -> _Drive | _Wait | _Finish:
        last = state.last_turn
        if last is None:
            return _Drive(1, self.claim.input_text)
        if not last.completed:
            return _Drive(last.index, await self._prompt_of(last), redrive=True)
        deferred = state.deferred.get(last.index, ())
        undecided = state.undecided(deferred)
        if undecided:
            return _Wait(undecided[0])
        parts: list[str] = []
        if deferred:
            parts.append(
                _decisions_note({ix: state.decision(ix) or "expired" for ix in deferred}, state.said)
            )
        queued = state.queued_inputs
        for seq in queued:
            ref = state.queued.get(seq)
            if ref is not None:
                parts.append((await self.content.get(ref)).decode("utf-8"))
        if parts:
            return _Drive(last.index + 1, "\n\n".join(parts), consume=tuple(queued), injected=not queued)
        return _Finish(last)

    async def _prompt_of(self, turn: TurnRecord) -> str:
        if turn.prompt_ref is not None:
            return (await self.content.get(turn.prompt_ref)).decode("utf-8")
        texts: list[str] = []
        for seq in turn.inputs:
            ref = self.state.queued.get(seq)
            if ref is not None:
                texts.append((await self.content.get(ref)).decode("utf-8"))
        return "\n\n".join(texts) or self.claim.input_text

    async def _turn(self, runtime: AssembledEngineRun, profile: EngineProfile, step: _Drive) -> Disposition | None:
        turn_id = turn_id_for(self.claim.run_id, step.index - 1)
        self.sink.begin(turn_id, self.claim.fence)
        if not step.redrive:
            try:
                await self._start_turn(runtime, step)
            except EngineRefusal as refused:
                code = _code(refused)
                self.log.warning("engine.kernel.refused", reasonCode=code)
                return await self.fail(runtime, code, step.index)
        if runtime.wrap_up is not None:
            # Before the engine spends: its gateway calls then stop short of the reserve (ENGBUDGET).
            _ = await runtime.wrap_up.hold()
        restore, carried_from, carried_snapshot = self._restore(profile)
        tracking = _TrackingEndpoint(
            runtime.tools, self._before_call, self.sink.tool_result, self._calls, asked=self.sink.question,
            egress_flow=runtime.egress_flow,
        )
        pinned_fork = child_workspace(self.claim.spec)
        # A run moved to its successor (D6) runs the successor's image: the old image and contract pins name another.
        pinned_image = image_pins(self.claim.spec) if self.resumed_from is None else ImagePins()
        deliverable = await self._deliverable(runtime)
        try:
            environment = pinned_environment(self.claim.spec, workspace_root=runtime.controller.workspace_root)
        except EnvironmentInvalid as invalid:
            # C20.18: never run on part of the environment the agent's editor defined.
            self.log.warning("engine.environment.invalid", reasonCode=invalid.reason)
            return await self.fail(runtime, invalid.reason, step.index)
        if environment is not None and environment.ignored:
            self.log.info("engine.environment.ignored", keys=list(environment.ignored))
        ctx = EngineRunContext(
            tenant=self.claim.org_id,
            session_id=self.claim.session_id,
            run_id=self.claim.run_id,
            attempt_id=self.claim.attempt_id,
            fence=self.claim.fence,
            lease_holder=self.claim.attempt_id,
            profile=profile,
            tools=tracking,
            tool_binding=runtime.tool_binding,
            grant_size=runtime.grant_size,
            artifacts=runtime.artifacts,
            skills=runtime.skills,
            restore=restore,
            resume_summary=resume_summary(self.state),
            labels=runtime.labels,
            workspace=pinned_fork.workspace_id if pinned_fork is not None else None,
            inputs=self._inputs_to_seed(step),
            ledger=runtime.ledger,
            snapshot=self._restorable(runtime, carried_from, carried_snapshot),
            secrets=runtime.secrets,
            own_login=runtime.own_login,
            # Before the run's first turn only; a re-driven first turn restores the uncompacted state and compacts again.
            compact=self.claim.compact if step.index == 1 else None,
            image_pin=pinned_image.image,
            contract_pin=pinned_image.contract,
            usage=runtime.usage,
            checkout=runtime.checkout,
            sites=await runtime.sites() if runtime.sites is not None else (),
            result_cap_chars=runtime.result_cap_chars,
            resource_caps=runtime.resource_caps,
            budget=RunBudget.of_claim(self.claim),
            store_now_s=self.store_time.now_s,
            waits=self.waits,
            deliverable=deliverable.name,
            owed=functools.partial(_owed_by_size, deliverable),
            environment=environment.definition if environment is not None else None,
            environment_extension=environment.extension if environment is not None else {},
        )
        if step.redrive:
            # Before the prompt is built: a save the dead attempt never journaled is journaled here (EV2-F2).
            _ = await runtime.controller.recover_saves(
                ctx, journaled_revision=self.state.workspace_revisions.get(ctx.workspace_id)
            )
            ctx = replace(ctx, resume_summary=resume_summary(self.state))
        now_s = self.store_time.now_s()
        plan = ctx.budget.plan(now_s, profile.limits, metered=ctx.metered)
        presenter = self.sink.presenter
        if plan is not None and isinstance(presenter, ShowsTimePlan):
            # D4: the person sees the turn's time plan as it starts (shown once; a later turn's replaces it).
            shown = presenter.planned(self.sink.stamp(), plan)
            if shown:
                _ = await self.commit("progress", shown)
        task = self._task(
            runtime, step, turn_id, restore, await self._first_prompt(runtime, ctx, step),
            allowance_note(ctx.allowance(now_s), plan),
        )
        self.log.info(
            "engine.kernel.turn", turn=step.index, redrive=step.redrive, restore=restore is not None,
            followUp=step.index > 1 and not step.redrive,
        )
        try:
            outcome = await self._drive(runtime, ctx, task)
        except Exception as exc:
            fault = fault_of(exc)
            if fault is None:
                raise
            return await self._turn_fault(runtime, step, fault, exc)
        await self.sink.flush()
        return await self._after(runtime, step, outcome, tracking.unrelayed(), listed=tracking.listed, asked=tracking.asked)

    async def _turn_fault(
        self, runtime: AssembledEngineRun, step: _Drive, fault: RunFault, exc: BaseException
    ) -> Disposition | None:
        """What a fault that ended the turn does, by the degrade table (ADR-RUN-01 D6): a refusal fails the run, an
        outage waits, a lost turn or a busy writer releases the attempt for the next to resume, and a save the store
        refused fails the run, which keeps its last saved revision."""
        if isinstance(fault.error, SandboxOutOfMemory):
            self.log.warning("engine.kernel.out_of_memory", turn=step.index, memoryMb=fault.error.memory_mb)
            return await self.fail(runtime, fault.code, step.index, memory_limit_mb=fault.error.memory_mb)
        match fault.at_turn_boundary:
            case "sandbox_wait":
                return await self._sandbox_wait()
            case "store_wait":
                return await self._store_wait(exc)
            case "release":
                if fault.kind == "turn_lost":
                    # The sandbox died under a gateway call (F-6), or the broker that reaches it went away (P9 FM-2).
                    gone = "engine.kernel.engine_gone" if isinstance(fault.error, EngineGone) else "engine.kernel.sandbox_gone"
                    self.log.warning(gone, turn=step.index, errorType=type(exc).__name__)
                return await self.release(fault.code)
            case _:
                self.log.warning("engine.kernel.refused", reasonCode=fault.code, fault=fault.kind)
                return await self.fail(runtime, fault.code, step.index)

    def _inputs_to_seed(self, step: _Drive) -> tuple[EngineInput, ...]:
        """The turn's pinned inputs until a save of the turn is journaled: the seed comes first, and after it the
        engine may have changed ``in/``, which seeding again would undo."""
        turn = self.state.last_turn
        if turn is None or turn.index != step.index or turn.saved:
            return ()
        return tuple(turn.pins)

    async def _resolved_inputs(
        self, runtime: AssembledEngineRun
    ) -> tuple[tuple[EngineInput, ...], tuple[SkippedInput, ...]]:
        """The artifacts the run's input names, resolved as its actor, then the trigger file of a scheduled run
        (TR-03); an artifact named like the trigger file conflicts with it. R36COVER-FU-3: an artifact refused on its
        own (over the cap, or gone since it was handed over) is skipped and named, when the claim's Node keeps the
        skipped list (``skipped_inputs``); before, it failed the whole job with a line that named no file."""
        inputs: tuple[EngineInput, ...] = ()
        skipped: tuple[SkippedInput, ...] = ()
        if self.claim.input_artifacts:
            if runtime.inputs is None:
                raise EngineInputsUnsupported("the run names input artifacts and this worker resolves none")
            # The conversation's binding, never the stream's: a job's stream is its own, so the conversation's files
            # resolved under it were all "unavailable" and none could be named (R46FIX-2).
            binding = self.artifacts_binding
            if self.claim.journal_accepts(SKIPPED_INPUTS):
                inputs, skipped = await resolve_each(runtime.inputs, binding, self.claim.input_artifacts)
            else:
                inputs = await runtime.inputs.resolve(binding, self.claim.input_artifacts)
        trigger = self.claim.trigger_context
        if trigger is not None:
            if runtime.objects is None:
                raise EngineInputsUnsupported("the run carries a trigger context and this worker stages no files")
            scope = session_scope(self.claim.org_id, self.claim.session_id)
            inputs = distinct_inputs((*inputs, await trigger_input(runtime.objects, scope, self.claim.run_id, trigger)))
        self.log.info("engine.kernel.inputs", inputs=len(inputs), skipped=len(skipped), trigger=trigger is not None)
        return inputs, skipped

    async def _start_turn(self, runtime: AssembledEngineRun, step: _Drive) -> None:
        events: list[NewEvent] = []
        if step.consume:
            events.extend(NewEvent("input.consumed", {"messageSeq": seq}) for seq in step.consume)
        else:
            data: dict[str, JsonValue] = {
                "role": "user",
                "textRef": await put_ref(self.content, step.prompt.encode("utf-8")),
                "isFinal": False,
            }
            if step.injected:
                data["injected"] = True
            events.append(NewEvent("message.completed", data))
        started: dict[str, JsonValue] = {"turn": step.index}
        if step.index == 1 and (self.claim.input_artifacts or self.claim.trigger_context is not None):
            # Pinned with the turn: a re-driven turn seeds exactly these bytes.
            inputs, skipped = await self._resolved_inputs(runtime)
            started["inputs"] = [pin_data(item) for item in inputs]
            if skipped:
                started["skippedInputs"] = [skip_data(item) for item in skipped]
        events.append(NewEvent("turn.started", started))
        if step.index == 1:
            events.extend(self.sink.presenter.started(self.sink.stamp(), step.prompt))
        await afailpoint("engine.kernel.before_turn_start")
        _ = await self.commit("turn", events)

    def _restorable(
        self, runtime: AssembledEngineRun, carried_from: str | None, carried: ObjectRef | None
    ) -> ObjectRef | None:
        """The sandbox snapshot the turn starts from: the run's latest, else the carried run's; none once it is older
        than the run's snapshot TTL (OWD-12), whether or not the sweeper has deleted it yet. A snapshot admission
        pinned for a child was checked against the TTL there."""
        if self.state.snapshot is not None:
            snapshot, taken_at = self.state.snapshot, self.state.snapshot_at
        elif carried is not None and carried_from is not None:
            snapshot, taken_at = carried, self.session_runs.snapshot_taken_at(carried_from)
        else:
            return carried
        ttl = runtime.snapshot_ttl
        if ttl is None or taken_at is None:
            return snapshot
        age = self.store_time.now() - datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
        if age <= ttl:
            return snapshot
        self.log.info("engine.snapshot.expired", ageS=int(age.total_seconds()), ttlDays=ttl.days)
        return None

    def _restore(self, profile: EngineProfile) -> tuple[EngineStateBlob | None, str | None, ObjectRef | None]:
        """The run's last checkpoint; before its first completed turn, the state carried from the session (its own
        stream's runs, else what admission pinned for a ``code_task`` child), the run it came from and its
        snapshot."""
        if self.state.checkpoint is not None:
            return self.state.checkpoint, None, None
        if any(turn.completed for turn in self.state.turns):
            return None, None, None
        kind = profile.engine.kind
        carried = self.session_runs.carried(self.claim.run_id, principal=self.claim.spec.principal_ref, engine_kind=kind)
        found = (
            (carried[0], carried[1], self.session_runs.snapshot_of(carried[0]))
            if carried is not None
            else pinned_carry(self.claim.spec, engine_kind=kind)
        )
        if found is None:
            return None, None, None
        self.log.info("engine.kernel.state_carried", fromRunId=found[0], pinned=carried is None)
        return found[1], found[0], found[2]

    async def _first_prompt(self, runtime: AssembledEngineRun, ctx: EngineRunContext, step: _Drive) -> str | None:
        """Turn 1's task (``engine_task_prompt``): the user's message, then the workspace note over the pinned inputs
        and what else the workspace holds (G2C-2). ``None``: not turn 1, or its pinned bytes cannot be read."""
        turn = self.state.last_turn
        if step.index != 1 or turn is None or turn.index != 1:
            return None
        pins = tuple(turn.pins)
        if pins and runtime.objects is None:
            self.log.warning("engine.kernel.briefing_skipped", reasonCode="input_bytes_unreadable")
            return None
        objects = runtime.objects
        # C5.01: a large input is described by its size; its bytes are read only when the engine fetches it.
        on_read = runtime.controller.input_stage_max_bytes
        inputs = [
            BriefedFile(pin.name, b"", on_read=True, size_bytes=pin.content.size_bytes)
            if pin.content.size_bytes > on_read
            else BriefedFile(pin.name, await objects.get(ctx.object_scope, pin.content))
            for pin in pins
        ] if objects else []
        others = ()
        handoff: str | None = None
        references: tuple[Reference, ...] = ()
        if runtime.workspaces is not None:
            head = await runtime.workspaces.head(ObjectScope(ctx.tenant), ctx.workspace_id)
            others = earlier_files(head.files, (pin.name for pin in pins))
            if objects is not None and HANDOFF_PATH in head.files:
                document = await read_handoff(objects, ctx.object_scope, head)
                handoff = handoff_summary(document) if document is not None and is_open(document) else None
            record = head.files.get(STORAGE_RECORD_PATH)
            if objects is not None and record is not None:
                references = decode_record(await objects.get(ctx.object_scope, record.content))
        skipped = [(item.name, item.words) for item in turn.skipped]
        return engine_task_prompt(step.prompt, inputs, others, handoff=handoff, references=references, skipped=skipped)

    def _task(
        self,
        runtime: AssembledEngineRun,
        step: _Drive,
        turn_id: str,
        restore: EngineStateBlob | None,
        first_prompt: str | None = None,
        allowance: str = "",
    ) -> EngineTask | FollowUp:
        """``allowance``: what a turn may use, in plain terms (WP-70), told with the instructions a session opens with."""
        state = self.state
        body = first_prompt if first_prompt is not None else step.prompt
        if step.index == 1 and runtime.task_facts:
            body = f"{body}\n\n{runtime.task_facts}"
        if step.index == 1 and runtime.answer_language:
            # G-52: built at each attempt and never journaled with the task, so a re-driven turn says it once.
            body = f"{body}\n\n{runtime.answer_language}"
        prompt = f"{self._interrupted_note()}\n\n{body}" if step.redrive else body
        # No saved state to restore: a later turn lost the earlier ones, a re-driven one its own progress.
        lost_history = restore is None and (step.index > 1 or step.redrive)
        if step.index == 1 or lost_history:
            instructions = "\n\n".join(part for part in (runtime.instructions, allowance) if part)
            if lost_history:
                note = f"{RESUME_NOTE}\n\nWhat was done so far:\n{resume_summary(state)}"
                instructions = f"{instructions}\n\n{note}" if instructions else note
                self.log.warning("engine.kernel.resumed_from_workspace", reasonCode="state_missing", turn=step.index)
            return EngineTask(turn_id=turn_id, prompt=prompt, instructions=instructions)
        if allowance:
            # ADR-RUN-06 D4: the restored session still holds the first turn's time plan, whose clock times a
            # person's wait (40 minutes for a clone, once) put in the past; this turn's own plan replaces it.
            prompt = f"{prompt}\n\n{RENEWED_ALLOWANCE}{allowance}"
        return FollowUp(turn_id=turn_id, text=prompt)

    def _interrupted_note(self) -> str:
        turn = self.state.last_turn
        if turn is None:
            return INTERRUPTED_NOTE
        given = {"/" + item.path for item in turn.pins}
        saved = [path.lstrip("/") for path in turn.saved if path not in given]
        return f"{INTERRUPTED_NOTE}\n{SAVED_NOTE}{', '.join(saved)}." if saved else INTERRUPTED_NOTE

    async def _drive(self, runtime: AssembledEngineRun, ctx: EngineRunContext, task: EngineTask | FollowUp) -> EngineTurnOutcome:
        turn = asyncio.ensure_future(runtime.controller.run_turn(ctx, task))
        watchers = [
            asyncio.ensure_future(self._watch(runtime, ctx, turn)),
            asyncio.ensure_future(self._flusher(turn)),
        ]
        try:
            return await turn
        finally:
            for watcher in watchers:
                _ = watcher.cancel()
            for watcher in watchers:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await watcher

    async def _watch(self, runtime: AssembledEngineRun, ctx: EngineRunContext, turn: asyncio.Future[EngineTurnOutcome]) -> None:
        """Cancel from ``run.cancel_requested``; interrupt on a drain or a lost lease (the turn is not over)."""
        wakeup = self.k.interactions
        while not turn.done():
            if wakeup is not None:
                _ = await wakeup.wait(self.claim.run_id, self.k.poll_s)
            else:
                await self.k.sleep(self.k.poll_s)
            if turn.done():
                return
            reason = self._stop_reason()
            if reason is None:
                try:
                    await self.sync()
                except (JournalGone, LeaseLost, OwnershipLost):
                    reason = "lease_lost"
                else:
                    reason = CANCEL_REQUESTED if self.state.cancel_requested else self._stop_reason()
            if reason is None:
                await self._relay_decisions(runtime, ctx)
                continue
            if reason == CANCEL_REQUESTED:
                self.log.info("engine.kernel.cancel", reason=reason)
                stopped = await runtime.controller.cancel(ctx, reason=reason)
            else:
                self._interrupted = reason
                self.log.info("engine.kernel.interrupt", reason=reason)
                stopped = await runtime.controller.interrupt(ctx, reason=reason)
            if stopped:
                return

    async def _relay_decisions(self, runtime: AssembledEngineRun, ctx: EngineRunContext) -> None:
        """ADR-RUN-06 D2: a decision on a call this turn deferred reaches the turn while it still runs, so the engine
        folds the result in and goes on: steered where the engine can be, else on the next result it is handed. A
        turn that ends first is told at its next turn, as before."""
        tracking = ctx.tools
        if not isinstance(tracking, _TrackingEndpoint):
            return
        decided = {ix: decision for ix in tracking.waiting() if (decision := self.state.decision(ix)) is not None}
        if not decided:
            return
        note = _decisions_note(decided, self.state.said)
        steered = await runtime.controller.tell(ctx, note)
        if steered:
            tracking.relayed.update(decided)
        else:
            tracking.carry(note, decided)
        self.log.info("engine.kernel.decision_relayed", interactions=len(decided), via="steer" if steered else "result")

    def _stop_reason(self) -> str | None:
        if self.lease.lost_reason is not None or self.lease.expired():
            return "lease_lost"
        if self._draining:
            return "drain"
        return None

    async def _flusher(self, turn: asyncio.Future[EngineTurnOutcome]) -> None:
        while not turn.done():
            await self.k.sleep(self.k.flush_interval_s)
            if not turn.done() and self._stop_reason() is None:
                await self.sink.flush()

    # ---- after a turn ----

    async def _after(
        self,
        runtime: AssembledEngineRun,
        step: _Drive,
        outcome: EngineTurnOutcome,
        deferred: list[str],
        *,
        listed: bool = True,
        asked: Sequence[AskedQuestion] = (),
    ) -> Disposition | None:
        self.log.info(
            "engine.kernel.turn_ended", turn=step.index, reason=outcome.reason, ended=outcome.ended,
            cancelled=outcome.cancelled, limit=outcome.limit, restored=outcome.restored,
            restoreIssue=outcome.restore_issue, deferred=len(deferred), interrupted=self._interrupted,
        )
        if self._interrupted == "lease_lost":
            return Disposition("lost", "lease lost during the turn")
        if outcome.cancelled:
            return await self._cancelled(runtime, step.index)
        if self._interrupted == "drain":
            return await self._yield("drain")
        if outcome.limit is not None:
            return await self._cut_short(runtime, step, outcome, _CUT_SHORT_BY_LIMIT[outcome.limit])
        if outcome.reason == "completed" and runtime.grant_size and not listed:
            # The engine ran without its granted tools (their gateway or Node was out of reach when it started):
            # its answer would claim work it could not do, so one more attempt drives the turn again.
            retry = self.claim.attempt <= 1
            self.log.warning(
                "engine.kernel.tools_unreachable", turn=step.index, tools=runtime.grant_size,
                attempt=self.claim.attempt, outcome="retry" if retry else "failed",
            )
            if retry:
                return await self.release(ENGINE_TOOLS_UNREACHABLE)
            return await self.fail(runtime, ENGINE_TOOLS_UNREACHABLE, step.index)
        if outcome.reason == "completed":
            return await self._end_turn(runtime, step, outcome, deferred, asked=asked)
        failed = outcome.ended and outcome.reason == "failed"
        budget_stop = failed and await self._budget_stop(runtime, outcome)
        if failed and not budget_stop and self.state.undecided(deferred):
            # An engine may end its turn failed because a call came back deferred for an approval (Codex 0.160 does
            # past the gateway's hold): the turn waits for the person as a completed one would (FIX-P2-B3).
            self.log.warning("engine.kernel.failed_turn_on_approval", turn=step.index, errorCode=outcome.error_code, deferred=len(deferred))
            return await self._end_turn(runtime, step, outcome, deferred)
        if failed:
            if budget_stop:
                # The gateway refused the next call for want of spend: the turn's work so far is the result.
                return await self._cut_short(runtime, step, outcome, "budget")
            if outcome.error_code in TRANSIENT_PROVIDER_CODES:
                return await self._provider_wait(runtime, step, outcome)
            code = outcome.error_code or ENGINE_FAILED
            cut = engine_stop_cut(code)
            if cut == SECRET_BLOCKED_CUT:
                self.log.warning("engine.kernel.secret_blocked", errorCode=code)
                return await self._cut_short(runtime, step, outcome, cut, unanswered_fails=SECRET_BLOCKED_CUT)
            if cut is not None:
                return await self._engine_error(runtime, step, outcome, code)
            return await self.fail(runtime, code, step.index)
        # The engine or its sandbox went away mid-turn: the next attempt resumes the turn.
        return await self.release("engine_turn_lost")

    async def _end_turn(
        self,
        runtime: AssembledEngineRun,
        step: _Drive,
        outcome: EngineTurnOutcome,
        deferred: list[str],
        *,
        listed: bool = True,
        asked: Sequence[AskedQuestion] = (),
    ) -> Disposition | None:
        events = self._checkpoint_events(self.sink.checkpoint or outcome.checkpoint)
        if outcome.compacted is not None and self.claim.journal_accepts(CONTEXT_COMPACTED):
            focus = self.claim.compact.focus if self.claim.compact is not None else None
            events.append(compacted_event(self._engine_kind(), outcome.compacted, focus))
        if outcome.auto_compacted is not None and self.claim.journal_accepts(CONTEXT_COMPACTED_AUTO):
            events.append(compacted_event(self._engine_kind(), outcome.auto_compacted, None, trigger="auto"))
        events.append(await self._response(outcome))
        events.append(NewEvent("turn.completed", {"turn": step.index}))
        if deferred:
            events.append(NewEvent("loop.state_saved", {"slots": {DEFERRED_SLOT: {str(step.index): list(deferred)}}}))
        await self.sync()
        undecided = self.state.undecided(deferred)
        await afailpoint("engine.kernel.before_turn_commit")
        if undecided:
            try:
                _ = await self.commit("turn_end", events, Suspend("approval", undecided[0]))
            except InteractionResolved:
                _ = await self.commit("turn_end", events)
                return None
            self.log.info("engine.kernel.suspend", reason="approval", interactionId=undecided[0])
            return Disposition("suspended", undecided[0])
        if not deferred and not self.state.queued_inputs:
            await self._close_handoff(runtime)
            if asked:
                # The person answers in the session's next run, which carries this engine's state (C3.70): a reply
                # that arrives before this commit is queued input instead, and the run takes it as its next turn.
                done = await self._complete(events, outcome.final_text or asked[-1].question, outcome="needs_input")
            else:
                answer, spent, settled = await self._settle_end(runtime, outcome)
                events[-1:-1] = spent
                done = await self._complete(events, answer, settled=settled)
            await afailpoint("engine.kernel.after_turn_commit")
            return done
        _ = await self.commit("turn_end", events)
        await afailpoint("engine.kernel.after_turn_commit")
        return None

    def _checkpoint_events(self, blob: EngineStateBlob | None) -> list[NewEvent]:
        if blob is None or self.sink.turn is None:
            return []
        return [NewEvent("engine.checkpointed", checkpoint_data(blob, self.sink.turn.turn_id), critical=False)]

    def _engine_kind(self) -> str:
        return str(getattr(getattr(self.claim.spec.executor, "engine", None), "kind", ENGINE))

    async def _response(self, outcome: EngineTurnOutcome) -> NewEvent:
        engine = self.claim.spec.executor
        kind = getattr(getattr(engine, "engine", None), "kind", ENGINE)
        return NewEvent(
            "model.response",
            {
                "messageRef": await put_ref(self.content, encode_assistant(AssistantMessage(content=[TextPart(text=outcome.final_text)]))),
                "toolCalls": [],
                "usage": {
                    "inputTokens": outcome.input_tokens,
                    "outputTokens": outcome.output_tokens,
                    "cacheReadTokens": outcome.cache_read_tokens,
                    "cacheWriteTokens": outcome.cache_write_tokens,
                    # The ledger's counts follow the harness convention; an engine's own report may not.
                    **({"inputIncludesCached": True} if outcome.usage_source == "ledger" else {}),
                },
                "provider": _ENGINE_PROVIDER,
                "transport": str(kind),
            },
        )

    async def _complete_after(self, turn: TurnRecord) -> Disposition | None:
        text = ""
        if turn.response_ref is not None:
            text = decode_assistant(await self.content.get(turn.response_ref)).text
        return await self._complete([], text)

    async def _cut_short(
        self,
        runtime: AssembledEngineRun,
        step: _Drive,
        outcome: EngineTurnOutcome,
        reason: CutShort,
        *,
        unanswered_fails: str | None = None,
    ) -> Disposition | None:
        """A limit ended the engine's turn (BUDGETEND): the run completes, flagged, rather than failing. The branch
        records what the run produced in its ledger, settles one outcome and asks the presenter for the words
        (ADR-RUN-01 D1).

        WP-81: when the engine already wrote the file the request asked for, that file is the result: no wrap-up
        rewrites it (a 143 KB report.md was once replaced by a 26 KB rewrite from tool-result scraps). Otherwise
        the run's wrap-up answers from what was gathered (WP-70: an engine cut mid-work leaves a progress remark as
        often as an answer), and a requested file out/ does not hold is written from that answer, and only that file.
        Without an answer, a file still owed is never finalized as it stands."""
        deliverable = await self._deliverable(runtime)
        calls = await self._done_calls()
        ledger, head = await self._outcome_ledger(
            runtime, deliverable, calls, limit=reason, refused=outcome.save_refusals
        )
        owed = ledger.owed_after_cut(deliverable) if head is not None else None
        written = ledger.written(deliverable) if head is not None and owed is None else None
        body = await self._file_text(runtime, written) if written is not None else ""
        spent: list[NewEvent] = []
        wrapped = False
        if written is not None and body:
            _ = await self._handoff(runtime, ledger, reason, body, calls, deliverable, wrote_outputs=True)
            _ = await runtime.artifacts.finalize(self.artifacts_binding)
            settled = settle(ledger, file=replace(written, state="final"), owed=None, kept=_kept(ledger))
        else:
            answered: str | None = None
            if runtime.wrap_up is not None:
                answered, spent = await self._wrap_up_answer(runtime, reason, draft=outcome.final_text.strip())
            wrapped = bool(answered)
            if unanswered_fails is not None and not wrapped:
                # WP-84: an engine error leaves only the engine's last text, which is no answer: without the
                # wrap-up's, the run fails with the engine's code (its wrap-up call, if any, still journaled).
                return await self.fail(runtime, unanswered_fails, step.index, spent)
            body = answered or ("" if owed is not None else outcome.final_text.strip())
            file = await self._handoff(
                runtime, ledger, reason, body, calls, deliverable,
                wrote_outputs=bool(outcome.published) and owed is None, synthesized=wrapped,
            )
            # What the engine published before the limit is its result, not a draft; a placeholder the run could not
            # replace stays a draft (WP-70).
            keep = frozenset({owed}) if owed is not None and file is None else frozenset[str]()
            _ = await runtime.artifacts.finalize(self.artifacts_binding, keep_drafts=keep)
            settled = settle(ledger, file=file, owed=owed, kept=_kept(ledger))
        framed = PRESENTER.frame(settled, body=body)
        answer = framed.text
        self.log.warning(
            "engine.kernel.cut_short", reason=reason, limit=outcome.limit, partial=bool(body), wrappedUp=wrapped,
            saved=settled.delivered.file is not None, owed=owed is not None, written=written is not None and not wrapped,
            action=settled.action,
        )
        events = [
            *self._checkpoint_events(self.sink.checkpoint or outcome.checkpoint),
            await self._response(replace(outcome, final_text=answer)),
            *spent,
            NewEvent("turn.completed", {"turn": step.index}),
        ]
        return await self._complete(
            events, answer, truncated_by=reason, budget_scope=await self._budget_scope(runtime, reason),
            settled=_Settled(settled, ledger, bool(body), framed),
        )

    async def _engine_error(
        self, runtime: AssembledEngineRun, step: _Drive, outcome: EngineTurnOutcome, code: str
    ) -> Disposition | None:
        """WP-84: the engine ended its turn on an error of its own. When it wrote the requested file, or its wrap-up
        answers from what it gathered, the run completes cut short by that error (``_cut_short``'s path, WP-77 D3);
        with nothing to answer from it fails with ``code``, and a parent keeps its notice of the kept work."""
        self.log.warning("engine.kernel.engine_error", errorCode=code)
        return await self._cut_short(runtime, step, outcome, ENGINE_ERROR_CUT, unanswered_fails=code)

    async def _settle_end(
        self, runtime: AssembledEngineRun, outcome: EngineTurnOutcome
    ) -> tuple[str, list[NewEvent], _Settled]:
        """WP-70: a turn that ended normally. While the requested file is still missing or a placeholder, the run
        writes it from what was gathered (its wrap-up, on the reserve it holds) and publishes it final over the
        placeholder; without a wrap-up answer the placeholder stays a draft and the answer says what exists: the work
        it saved for a "continue", or that nothing was gathered. The answer, the wrap-up's usage events,
        and the settled outcome."""
        deliverable = await self._deliverable(runtime)
        calls = await self._done_calls()
        ledger, head = await self._outcome_ledger(
            runtime, deliverable, calls, limit=None, refused=outcome.save_refusals
        )
        owed = ledger.owed_at_end(deliverable) if head is not None else None
        if owed is None:
            if head is not None and _owed_by_size(deliverable, head.files) is not None:
                # A short file the engine wrote and called done is its deliverable: the draft it left settles now.
                _ = await runtime.artifacts.finalize(self.artifacts_binding)
            settled = settle(ledger, file=ledger.written(deliverable), owed=None)
            return outcome.final_text, [], _Settled(settled, ledger, True)
        self.log.warning("engine.kernel.owed_at_end", wrapUp=runtime.wrap_up is not None)
        answered: str | None = None
        spent: list[NewEvent] = []
        if runtime.wrap_up is not None:
            answered, spent = await self._wrap_up_answer(runtime, UNFINISHED_END, draft=outcome.final_text.strip())
        file = await self._handoff(
            runtime, ledger, UNFINISHED, answered or "", calls, deliverable, wrote_outputs=False, synthesized=True
        )
        settled = settle(ledger, file=file if answered else None, owed=owed, kept=_kept(ledger))
        framed = PRESENTER.frame(settled, body=answered or "")
        return framed.text, spent, _Settled(settled, ledger, file is not None, framed)

    async def _deliverable(self, runtime: AssembledEngineRun) -> Deliverable:
        """What the run owes, read once per attempt (ADR-RUN-01 D1): the deliverable admission pinned (a run admitted
        before the pin resolves its own request once), or for a continuation whose message asks for no file
        ("continue"), the file the open handoff's task was pinned to."""
        if self._pinned is not None:
            return self._pinned
        pinned = pinned_deliverable(self.claim.spec, self.claim.input_text)
        if not pinned.is_file:
            try:
                found = await self._earlier_handoff(runtime)
            except Exception as exc:
                self.log.warning("engine.handoff.unreadable", errorType=type(exc).__name__)
                found = None
            pinned = (handoff_deliverable(found[1]) if found is not None else None) or pinned
        self._pinned = pinned
        return pinned

    async def _outcome_ledger(
        self,
        runtime: AssembledEngineRun,
        deliverable: Deliverable,
        calls: Sequence[DoneCall],
        *,
        limit: CutShort | None,
        refused: Sequence[str] = (),
    ) -> tuple[OutcomeLedger, WorkspaceManifest | None]:
        """The run's ledger as it ends: its steps, the sources it gathered, the files and notes its workspace holds,
        the saves the store refused during the turn (``refused``: their reasons), and the limit that stopped it. The head it read; ``None`` when the workspace cannot be read, and then no file
        is taken to be owed (the engine's outputs stand)."""
        ledger = OutcomeLedger()
        _ = ledger.record(*(StepCompleted(turn.index) for turn in self.state.turns if turn.completed))
        # ADR-RUN-01 D3: the sources the run read, as the session's provenance ledger names them (web pages, repository
        # files, records, connector items); a call none of them accounts for, as before, by its own address or digest.
        named = gathered_sources(self.ledger, self.claim.run_id)
        _ = ledger.record(*named)
        known = {fact.url for fact in named if fact.url is not None}
        for call in calls:
            url, title = source_of(call)
            if url is not None and url in known:
                continue
            _ = ledger.record(SourceGathered(url or f"{call.tool}:{call.args_digest}", call.tool, url, title))
        _ = ledger.record(*(SaveRefused(why[:200]) for why in refused))
        if limit is not None:
            _ = ledger.record(LimitReached(limit))
        head = await self._head(runtime)
        if head is None:
            return ledger, None
        texts: dict[str, str] = {}
        for path, entry in head.files.items():
            small = entry.content.size_bytes < PLACEHOLDER_MAX_BYTES and not entry.is_symlink
            if small and deliverable.name is not None and path.rsplit("/", 1)[-1] == deliverable.name:
                text = await self._read(runtime, entry)
                if text is not None:
                    texts[path] = text
        _ = ledger.record(*workspace_artifacts(head.files, head.revision, deliverable, texts=texts))
        _ = ledger.record(*(NoteSaved(path) for path, entry in head.files.items() if is_engine_note(path) and not entry.is_symlink))
        return ledger, head

    async def _head(self, runtime: AssembledEngineRun) -> WorkspaceManifest | None:
        if runtime.workspaces is None:
            return None
        try:
            await self.sync()
            return await runtime.workspaces.head(ObjectScope(self.claim.org_id), self._workspace_id())
        except Exception as exc:
            self.log.warning("engine.kernel.outputs_unread", errorType=type(exc).__name__)
            return None

    async def _read(self, runtime: AssembledEngineRun, entry: WorkspaceFile) -> str | None:
        if runtime.objects is None:
            return None
        try:
            raw = await runtime.objects.get(session_scope(self.claim.org_id, self.claim.session_id), entry.content)
        except Exception:  # an unreadable file is not taken for a deliverable
            return None
        return raw.decode("utf-8", errors="replace")

    async def _file_text(self, runtime: AssembledEngineRun, artifact: ArtifactWritten) -> str:
        """WP-81: the text of the file the engine wrote; "" when it cannot be read (the wrap-up then answers)."""
        head = await self._head(runtime)
        entry = head.files.get(artifact.path) if head is not None else None
        text = await self._read(runtime, entry) if entry is not None else None
        if text is None:
            self.log.warning("engine.kernel.outputs_unread", errorType="unreadable")
        return (text or "").strip()

    async def _budget_scope(self, runtime: AssembledEngineRun, reason: CutShort) -> str | None:
        """Which shared budget cut the turn short (C9.06): the gateway saw Node's refusal, this kernel did not, so it
        reads which budget binds the run from the run's account (WP56-STOP-1). None: another limit, or the run's own."""
        if reason != "budget":
            return None
        ledger = await self._ledger(runtime)
        return ledger.budget_scope if ledger is not None else None

    async def _handoff(
        self,
        runtime: AssembledEngineRun,
        ledger: OutcomeLedger,
        reason: PartialReason,
        answer: str,
        calls: Sequence[DoneCall],
        deliverable: Deliverable,
        *,
        wrote_outputs: bool,
        synthesized: bool = False,
    ) -> ArtifactWritten | None:
        """SPENDCONT: what the stopped run did, saved in its workspace for the run that continues it (the handoff,
        the results its tools returned, the partial answer and, when the request asked for a file the engine did not
        write, that file marked partial), recorded in the ledger. Nothing is saved when there is nothing to continue
        from. The file it wrote in place of the deliverable, if any. Never fails the run: without a
        workspace, or when the save fails, the run completes with its answer alone."""
        if runtime.workspaces is None or runtime.objects is None or not (answer.strip() or ledger.sources or ledger.notes):
            return None
        try:
            await self.sync()
            saved = await save_handoff(
                store=runtime.workspaces,
                objects=runtime.objects,
                publisher=runtime.artifacts,
                binding=self.artifacts_binding,
                object_scope=session_scope(self.claim.org_id, self.claim.session_id),
                workspace_id=self._workspace_id(),
                request=self.claim.input_text,
                answer=answer,
                reason=reason,
                calls=calls,
                wrote_outputs=wrote_outputs,
                derivation=Derivation(task_id=self.claim.run_id, source_records=None),
                deliverable=deliverable,
                banner=PRESENTER.banner(reason, synthesized=synthesized),
            )
            await self.sink.workspace_saved(self.binding, saved.manifest, "turn_end")
        except Exception as exc:
            fault = fault_of(exc)
            if fault is not None and fault.kind == "save_refused":
                _ = ledger.record(SaveRefused(str(fault.error)[:200] or fault.code))
            self.log.warning("engine.handoff.failed", reason=reason, errorType=type(exc).__name__)
            return None
        self.log.info(
            "engine.handoff.saved", reason=reason, revision=saved.manifest.revision, deliverable=saved.deliverable is not None
        )
        _ = ledger.record(NoteSaved(HANDOFF_PATH), *((NoteSaved(PARTIAL_ANSWER_PATH),) if answer.strip() else ()))
        if saved.deliverable is None:
            return None
        path = f"{OUTPUTS_DIR}/{saved.deliverable}"
        entry = saved.manifest.files.get(path)
        file = ArtifactWritten(
            path, saved.manifest.revision, entry.content.size_bytes if entry is not None else None, "final", "wrap_up"
        )
        _ = ledger.record(file)
        return file

    async def _close_handoff(self, runtime: AssembledEngineRun) -> None:
        """A run that completes after an earlier one was cut short marks that handoff finished, so a later task of the
        conversation is not told to continue it. Best effort, as the handoff itself is."""
        try:
            found = await self._earlier_handoff(runtime)
            if found is None or runtime.workspaces is None or runtime.objects is None:
                return
            head, document = found
            manifest = await close_handoff(
                runtime.workspaces, runtime.objects, session_scope(self.claim.org_id, self.claim.session_id),
                ObjectScope(self.claim.org_id), head, document, run_id=self.claim.run_id,
            )
            if manifest is not None:
                await self.sink.workspace_saved(self.binding, manifest, "turn_end")
        except Exception as exc:
            self.log.warning("engine.handoff.close_failed", errorType=type(exc).__name__)

    async def _done_calls(self) -> list[DoneCall]:
        """The run's successful tool calls, in call order, with their result text and, where this attempt saw them,
        their arguments."""
        done: list[DoneCall] = []
        for invocation in self.state.invocations.values():
            if invocation.status != "ok" or invocation.result_ref is None:
                continue
            try:
                raw = await self.content.get(invocation.result_ref)
            except Exception as exc:
                self.log.warning("engine.result_unreadable", errorType=type(exc).__name__)
                continue
            seen = self._calls.get(invocation.invocation_id)
            name = (invocation.tool_id or invocation.name).rsplit("/", 1)[-1]
            done.append(DoneCall(name, invocation.args_digest, _result_text(raw), seen[1] if seen is not None else None))
        return done

    async def _wrap_up_answer(
        self, runtime: AssembledEngineRun, reason: CutShort, *, draft: str = ""
    ) -> tuple[str | None, list[NewEvent]]:
        """The run's request answered from the tool results it gathered, on its wrap-up reserve; ``None`` without a
        wrap-up or a result to answer from. With it, the ``usage.recorded`` of that call as the ledger holds it: no
        turn's ``model.response`` counts it, and the run's journal usage must add up to its ledger's (C9.01)."""
        if runtime.wrap_up is None:
            return None, []
        await self.sync()
        gathered = await self._gathered()
        if not gathered:
            return None, []
        # WP-70: what the engine kept besides its tool results, read first; its last text as an unfinished draft.
        gathered = [*await self._engine_notes(runtime), *gathered]
        if draft:
            gathered.append(GatheredResult(ENGINE_DRAFT, draft))
        request, earlier = await self._earlier_partial(runtime)
        before = await self._ledger(runtime)
        text = await runtime.wrap_up.answer(request, gathered, reason, earlier=earlier)
        after = await self._ledger(runtime) if before is not None else None
        if before is None or after is None or after.used == before.used:
            return text, []
        used = after.used - before.used
        cost = max(0, after.committed.usd_micros - before.committed.usd_micros) / MICRO_USD
        return text, [
            NewEvent(
                "usage.recorded",
                {
                    "tokens": {
                        "inputTokens": used.input_tokens,
                        "outputTokens": used.output_tokens,
                        "cacheReadTokens": used.cache_read_tokens,
                        "cacheWriteTokens": used.cache_write_tokens,
                        "inputIncludesCached": True,
                    },
                    "costUsd": cost,
                },
            )
        ]

    async def _budget_stop(self, runtime: AssembledEngineRun, outcome: EngineTurnOutcome) -> bool:
        """Whether the failed turn is the budget's cut: the engine passed the gateway's budget code on, or the run's
        ledger says its spend is at a cap. An engine need not pass the code on (Qwen Code 0.26.5 over ACP reports its
        own error, R31JOURNEY-FU-8), so the ledger, which the gateway's refusal was read from, decides."""
        if outcome.error_code in BUDGET_STOP_CODES:
            return True
        ledger = await self._ledger(runtime)
        if ledger is None or not ledger.exhausted:
            return False
        self.log.warning("engine.kernel.budget_spent", errorCode=outcome.error_code or ENGINE_FAILED)
        return True

    async def _ledger(self, runtime: AssembledEngineRun) -> Remaining | None:
        if runtime.usage is None:
            return None
        try:
            return await runtime.usage.read()
        except Exception as exc:
            self.log.warning("engine.kernel.usage_unreadable", reasonCode="usage_unreadable", errorType=type(exc).__name__)
            return None

    def _workspace_id(self) -> str:
        """The workspace the controller writes: the child's pinned fork, else the session's."""
        fork = child_workspace(self.claim.spec)
        return fork.workspace_id if fork is not None else session_workspace_id(self.claim.session_id)

    async def _earlier_handoff(self, runtime: AssembledEngineRun) -> tuple[WorkspaceManifest, dict[str, JsonValue]] | None:
        """The handoff an earlier cut-short run left in this run's workspace, with the head it was read from."""
        if runtime.workspaces is None or runtime.objects is None:
            return None
        head = await runtime.workspaces.head(ObjectScope(self.claim.org_id), self._workspace_id())
        scope = session_scope(self.claim.org_id, self.claim.session_id)
        document = await read_handoff(runtime.objects, scope, head)
        return (head, document) if document is not None and is_open(document) else None

    async def _earlier_partial(self, runtime: AssembledEngineRun) -> tuple[str, str | None]:
        """SPENDCONT: the task's request and what an earlier cut-short attempt at it produced, which this run's
        wrap-up extends (a continuation's own message is often just "continue")."""
        request = self.claim.input_text
        try:
            found = await self._earlier_handoff(runtime)
            if found is None or runtime.objects is None:
                return request, None
            head, document = found
            partial = await earlier_partial(
                runtime.objects, session_scope(self.claim.org_id, self.claim.session_id), head, document
            )
            return original_request(document, request), partial
        except Exception as exc:
            self.log.warning("engine.handoff.unreadable", errorType=type(exc).__name__)
            return request, None

    async def _gathered(self) -> list[GatheredResult]:
        """The run's successful tool results, in call order, as text, each with the page it came from (WP-70)."""
        return [
            GatheredResult(call.tool, call.text, source_of(call)[0]) for call in await self._done_calls() if call.text
        ]

    async def _engine_notes(self, runtime: AssembledEngineRun) -> list[GatheredResult]:
        """WP-70: the notes the engine kept in its workspace (text files outside in/, out/ and the handoff's own),
        smallest first, bounded; none when the workspace cannot be read."""
        if runtime.workspaces is None or runtime.objects is None:
            return []
        try:
            head = await runtime.workspaces.head(ObjectScope(self.claim.org_id), self._workspace_id())
            scope = session_scope(self.claim.org_id, self.claim.session_id)
            found = sorted(
                (entry.content.size_bytes, path, entry)
                for path, entry in head.files.items()
                if is_engine_note(path) and not entry.is_symlink and entry.content.size_bytes <= NOTE_MAX_BYTES
            )
            notes: list[GatheredResult] = []
            left = NOTES_MAX_BYTES
            for size, path, entry in found:
                if size > left:
                    continue
                left -= size
                text = (await runtime.objects.get(scope, entry.content)).decode("utf-8", errors="replace").strip()
                if text:
                    notes.append(GatheredResult(ENGINE_NOTES, text, path))
            return notes
        except Exception as exc:
            self.log.warning("engine.kernel.notes_unread", errorType=type(exc).__name__)
            return []

    async def _complete(
        self,
        events: list[NewEvent],
        final_text: str,
        *,
        truncated_by: CutShort | None = None,
        outcome: Literal["answered", "needs_input"] = "answered",
        budget_scope: str | None = None,
        settled: _Settled | None = None,
    ) -> Disposition | None:
        """Complete the run (with the turn's events); ``None``: input was queued meanwhile, a follow-up takes it.
        ``settled``: the run's outcome, its ledger and whether its answer holds a result, journaled with it and
        carried in a child's result for its parent's briefing (ADR-RUN-01 D1)."""
        final_text = bounded_text(self._linked(final_text), ANSWER_MAX_BYTES)
        # WP-89: a chat that words the settled outcome itself is sent the model's prose; the parent's model, a
        # surface with no UI, still reads the whole text (``_as_child``).
        worded = settled is not None and settled.framed is not None and self._outcome_worded()
        shown = (
            bounded_text(self._linked(settled.framed.prose), ANSWER_MAX_BYTES)
            if worded and settled and settled.framed
            else final_text
        )
        ending = [
            *events,
            *(self._outcome_events(settled) if settled is not None else ()),
            NewEvent(
                "message.completed",
                {"role": "assistant", "textRef": await put_ref(self.content, shown.encode("utf-8")), "isFinal": True},
            ),
            *(await self._verified(shown) if outcome == "answered" else ()),
        ]
        if self.sink.turn is not None:
            presenter = self.sink.presenter
            ending.extend(
                presenter.awaiting_answer(self.sink.stamp(), final_text)
                if outcome == "needs_input" and isinstance(presenter, AsksQuestions)
                else presenter.finished(
                    self.sink.stamp(),
                    success=True,
                    reason=None,
                    output=shown,
                    truncated_by=truncated_by,
                    budget_scope=budget_scope,
                    journal=await self._run_events(),
                )
            )
        transition = await self._as_child(Complete(outcome, truncated_by=truncated_by), final_text, settled)
        try:
            _ = await self.commit("complete", ending, transition)
        except InputPending:
            if events:
                _ = await self.commit("turn_end", events)
            self.log.info("engine.kernel.follow_up", reason="input_pending")
            return None
        self.log.info("engine.kernel.complete", outcome=outcome)
        return Disposition("completed", None if outcome == "answered" else outcome)

    def _linked(self, text: str) -> str:
        """MR-18: the engine's final text with its links to its own sandbox named by the files it published, once,
        before anything journals it: its message, its answer and a parent's copy all read the same words (a Codex
        ``sandbox:/mnt/data/out/f.py`` link reached the person's message and the job trail)."""
        presenter = self.sink.presenter
        return presenter.linked(text) if isinstance(presenter, LinksFiles) else text

    def _verify_with_workspace(self, runtime: AssembledEngineRun) -> VerifyPorts | None:
        """The host's verify ports, reading files from the workspace the run's turns save to (as a handoff does)."""
        if verify_gate(self.claim.spec) is None:
            return runtime.verify
        ports = runtime.verify or VerifyPorts()
        if ports.files is not None or runtime.workspaces is None or runtime.objects is None:
            return ports
        files = StoredWorkspaceFiles(
            runtime.workspaces,
            runtime.objects,
            ObjectScope(self.claim.org_id),
            self._workspace_id(),
            object_scope=session_scope(self.claim.org_id, self.claim.session_id),
        )
        return replace(ports, files=files)

    async def _verified(self, final_text: str) -> list[NewEvent]:
        """The run's verify gate (G-05) on the engine's final answer: flagged, never sent back (an engine's turn has
        ended; ``onFail: continue`` is the loop's)."""
        gate = verify_gate(self.claim.spec)
        if gate is None:
            return []
        try:
            verification = await RunVerifier(gate, self._verify_ports or VerifyPorts()).verify(
                final_text, may_continue=False
            )
        except (LeaseLost, OwnershipLost):
            raise
        except Exception as exc:
            self.log.warning("gate.verify_failed", errorType=type(exc).__name__)
            verification = Verification(
                (CheckResult("command", False, None, "The checks could not be run because of an internal error."),)
            )
        self.log.info(
            "gate.verified", status=verification.status, failed=[c.kind for c in verification.checks if not c.passed]
        )
        return [verification.event()]

    def _outcome_events(self, settled: _Settled) -> list[NewEvent]:
        """The ledger's facts this run has not journaled yet, and its settled outcome, for a journal that takes them."""
        if not self.claim.journal_accepts(OUTCOME_LEDGER):
            return []
        outcome, ledger, framed = settled.outcome, settled.ledger, settled.framed
        journaled = set(ledger_from_events(self.events, run_id=self.claim.run_id).facts)
        fresh = [fact for fact in ledger.facts if fact not in journaled]
        if framed is not None and self._outcome_worded():
            words = settled_event(outcome, self._pinned or ANSWER, closing=framed.closing, opening=framed.opening)
        else:
            words = settled_event(outcome, self._pinned or ANSWER)
        return [*recorded_events(fresh), words]

    def _outcome_worded(self) -> bool:
        """WP-89: the run's journal takes the settled outcome with its framing, and its chat words the outcome."""
        return self.claim.journal_accepts(OUTCOME_LEDGER) and self.claim.journal_accepts(OUTCOME_WORDS)

    async def _as_child(self, transition: Complete, output: str, settled: _Settled | None = None) -> Complete:
        if self.claim.spec.run_kind != "child":
            return transition
        document = child_result_document(outcome=transition.outcome, output=output, truncated_by=transition.truncated_by)
        if settled is not None:
            child: dict[str, JsonValue] = {**outcome_wire(settled.outcome), "answered": settled.answered}
            if settled.framed is not None:
                # WP-89: the parent that presents this answer as its own shows the prose and relays the outcome.
                child["presented"] = {"closing": settled.framed.closing[:CLOSING_MAX_CHARS], "opening": settled.framed.opening}
                child["deliverable"] = (self._pinned or ANSWER).wire()
            document[OUTCOME_KEY] = child
        if self._child_result is not None:
            document = self._child_result(document, await self._run_events())
        # WP-62: what the host adds (the sources the answer cites) is tool output, untrusted in size; a refused put
        # here failed a cut-short child and lost its work.
        document, cut = bounded_child_document(document)
        if cut:
            self.log.warning("engine.kernel.child_result_bounded", maxBytes=CHILD_RESULT_MAX_BYTES)
        ref = await self.content.put(canonical_json(cast("JsonValue", document)))
        return Complete(
            transition.outcome,
            child_result=ChildResultRef(result_ref=ref, labels={"integrity": "internal"}),
            truncated_by=transition.truncated_by,
        )

    async def _suspend_approval(self, interaction_id: str) -> Disposition | None:
        try:
            _ = await self.commit("suspend", [], Suspend("approval", interaction_id))
        except InteractionResolved:
            await self.sync()
            return None
        self.log.info("engine.kernel.suspend", reason="approval", interactionId=interaction_id)
        return Disposition("suspended", interaction_id)

    async def _settle_dead_effects(self, runtime: AssembledEngineRun) -> None:
        """An intent without an outcome from an earlier attempt: the effect may have happened."""
        events: list[NewEvent] = []
        for inv in self.state.invocations.values():
            if inv.state != "intent" or inv.intent_attempt_id == self.claim.attempt_id:
                continue
            if runtime.idempotent.get(inv.tool_id or "", False):
                events.append(
                    await self.effect_log.outcome_event(
                        inv.invocation_id, ToolExecution(status="error", content=_RECOVERED)
                    )
                )
                choice = "retry"
            else:
                events.append(self.effect_log.unknown_event(inv.invocation_id))
                choice = "unknown"
            self.log.info("engine.kernel.effect_recovered", invocationId=inv.invocation_id, toolId=inv.tool_id, choice=choice)
        if events:
            _ = await self.commit("recover", events)

    # ---- transitions ----

    async def fail(
        self,
        runtime: AssembledEngineRun,
        code: str,
        turn: int | None = None,
        spent: Sequence[NewEvent] = (),
        *,
        memory_limit_mb: int | None = None,
    ) -> Disposition:
        with contextlib.suppress(Exception):
            # The run will not resume: its open drafts never become finals.
            _ = await runtime.artifacts.discard_drafts(self.artifacts_binding)
        # A wrap-up call made before the run failed is charged on its ledger: its usage is journaled too (C9.01).
        events: list[NewEvent] = list(spent)
        if turn is not None and self.sink.turn is not None:
            events.extend(self.sink.presenter.finished(self.sink.stamp(), success=False, reason=code, output=""))
        self.log.warning("engine.kernel.failed", reasonCode=code)
        return await self.terminate(Fail(code, memory_limit_mb=memory_limit_mb), "failed", events)

    async def _cancelled(self, runtime: AssembledEngineRun, turn: int | None = None) -> Disposition:
        with contextlib.suppress(Exception):
            _ = await runtime.artifacts.discard_drafts(self.artifacts_binding)
        events: list[NewEvent] = []
        if self.sink.turn is not None:
            events.extend(self.sink.presenter.finished(self.sink.stamp(), success=False, reason="cancelled", output=""))
        self.log.info("engine.kernel.cancelled", turn=turn)
        return await self.terminate(Cancelled(), "cancelled", events)

    async def terminate(
        self, transition: Transition, status: DispositionStatus, events: Sequence[NewEvent] = ()
    ) -> Disposition:
        closing: list[NewEvent] = []
        if transition.kind in ("fail", "cancelled", "quarantine", "complete"):
            closing = await closing_outcomes(
                self.effect_log, self.state.invocations.values(), with_results=self.content.inner is not None
            )
            if closing:
                self.log.info("engine.kernel.calls_closed", calls=len(closing), status=status)
        _ = await self.commit(status, [*closing, *events], transition)
        if transition.kind in ("fail", "cancelled", "quarantine", "complete"):
            await self._reap({"orgId": self.claim.org_id, "runId": self.claim.run_id})
        return Disposition(status, getattr(transition, "error_type", None))

    def _preempted(self) -> bool:
        """The claimer gave this run's slot to a higher class (G-45): it yields before its next turn, never inside one,
        and only after this attempt drove a turn, so each claim moves it on."""
        asked = self.k.preempt
        return (
            asked is not None
            and self._turns_driven > 0
            and asked.requested(self.claim.run_id)
            and self.claim.journal_accepts(PRIORITY_PREEMPTION)
        )

    async def _yield(self, reason: Literal["drain", "preempted"]) -> Disposition:
        # A turn interrupted by the drain was checkpointed: the next attempt continues from that state.
        events = self._checkpoint_events(self.sink.checkpoint)
        not_before = db_timestamp(self.store_time.now() + timedelta(seconds=0))
        _ = await self.commit("yield", events, Yield(not_before, reason))
        self.log.info("engine.kernel.yield", reason=reason, checkpoint=bool(events))
        return Disposition("yielded", reason)

    async def _verify_capabilities(self, runtime: AssembledEngineRun) -> Disposition | None:
        """The first attempt journals what it was granted, in the loop's slot; a later attempt that finds a tool the
        run had withdrawn by its MCP server fails at once naming the server (WP-79 D3, WP-84): a server that answers
        without it will not bring it back, and the restored engine session would only hear the call was "removed".
        Any other difference is not this check's: a moved digest is refused at the build (CPD-7), and a sign-in that
        came or went is no capability."""
        if runtime.capabilities is None:
            return None
        observed = dict(await runtime.capabilities())
        recorded = next(
            (
                cast("dict[str, JsonValue]", slots[CAPABILITY_SLOT])
                for event in reversed(self.events)
                if event.type == "loop.state_saved"
                and isinstance(slots := event.data.get("slots"), dict)
                and isinstance(cast("dict[str, JsonValue]", slots).get(CAPABILITY_SLOT), dict)
            ),
            None,
        )
        if recorded is None:
            _ = await self.commit("capabilities", [NewEvent("loop.state_saved", {"slots": {CAPABILITY_SLOT: observed}})])
            self.log.info("engine.kernel.capabilities_recorded", count=len(observed))
            return None
        back = returned(recorded, observed)
        if back and runtime.withhold is not None:
            # The run never held these: they stay withheld until the next run offers them (WP79-FU-5).
            await runtime.withhold(cast("dict[str, JsonValue]", back))
            observed = dict(await runtime.capabilities())
            self.log.info("engine.kernel.tools_withheld", reason="returned", keys=sorted(back))
        changed = capability_changes(recorded, observed)
        servers = withdrawn_servers(observed, changed)
        if not servers:
            return None
        self.log.warning("engine.kernel.capability_lost", reason="tool_withdrawn", changed=changed, outcome="failed")
        return await self.terminate(Fail(TOOL_WITHDRAWN, PRESENTER.failure(Failed(TOOL_WITHDRAWN, Delivered(), servers=tuple(servers))), servers=tuple(servers)), "failed")

    async def _capability_lost(self, lost: CapabilityLost) -> Disposition:
        """A pinned capability the host cannot build (a store or server that does not answer, an approval that
        moved): as the loop's driver settles it (PA-7), a bounded re-check, then ``capability_lost``; never released
        as a crash, since retrying a build that cannot succeed only quarantines the run. An MCP tool its answering
        server no longer offers as approved never reaches here: the build withholds it (WP-79 D2)."""
        fields = {"reason": "pinned_capability_unavailable", "missing": list(lost.missing), "changed": list(lost.changed)}
        waited = await self.leased(lambda: capability_waits(self.k.journal, self.claim), "journal.read")
        delay = recheck_delay(waited)
        if delay is not None:
            self.log.info("engine.kernel.capability_wait", recheck=waited + 1, delayS=delay, **fields)
            not_before = db_timestamp(self.store_time.now() + timedelta(seconds=delay))
            _ = await self.commit("yield", [], Yield(not_before, CAPABILITY_WAIT))
            return Disposition("yielded", CAPABILITY_WAIT)
        self.log.warning("engine.kernel.capability_lost", rechecks=waited, outcome="failed", **fields)
        return await self.terminate(Fail(CAPABILITY_LOST), "failed")

    async def _sandbox_wait(self) -> Disposition:
        """The broker cannot make or reach a sandbox now: capacity, not a crash (QA31F). A bounded wait with
        backoff that never counts as a crash attempt, then a typed failure the user may retry."""
        waited = await self.leased(
            lambda: capability_waits(self.k.journal, self.claim, SANDBOX_WAIT), "journal.read"
        )
        delay = recheck_delay(waited, SANDBOX_RECHECK_S)
        if delay is None:
            self.log.warning("engine.kernel.sandbox_unavailable", waits=waited, outcome="failed")
            return await self.terminate(Fail(SANDBOX_UNAVAILABLE), "failed")
        self.log.warning("engine.kernel.sandbox_wait", wait=waited + 1, delayS=delay)
        events = self._checkpoint_events(self.sink.checkpoint)
        not_before = db_timestamp(self.store_time.now() + timedelta(seconds=delay))
        _ = await self.commit("yield", events, Yield(not_before, SANDBOX_WAIT))
        return Disposition("yielded", SANDBOX_WAIT)

    async def _provider_wait(
        self, runtime: AssembledEngineRun, step: _Drive, outcome: EngineTurnOutcome
    ) -> Disposition | None:
        """The model provider failed the turn in a way waiting can fix: a bounded backoff that never counts as a
        crash, the turn's checkpoint kept, so the next attempt re-drives it on the engine's restored session. Past
        the bound the run is cut short by the provider: it answers from what it gathered, labelled partial."""
        waited = await self.leased(lambda: capability_waits(self.k.journal, self.claim, LLM_BACKOFF), "journal.read")
        delay = ProviderWaits.of_count(waited).next_delay()
        if delay is None:
            self.log.warning("engine.kernel.provider_unavailable", waits=waited, errorCode=outcome.error_code, outcome="cut_short")
            return await self._cut_short(runtime, step, outcome, "provider")
        self.log.warning("engine.kernel.provider_wait", wait=waited + 1, delayS=delay, errorCode=outcome.error_code)
        events = self._checkpoint_events(self.sink.checkpoint or outcome.checkpoint)
        not_before = db_timestamp(self.store_time.now() + timedelta(seconds=delay))
        _ = await self.commit("yield", events, Yield(not_before, LLM_BACKOFF))
        return Disposition("yielded", LLM_BACKOFF)

    async def _store_wait(self, exc: BaseException) -> Disposition:
        """A store the run saves to cannot be reached (CH-09): the turn's work waits with backoff and is never counted
        as a crash; a later attempt re-drives the turn from its last checkpoint. Past the bound, a typed failure."""
        waited = await self.leased(
            lambda: capability_waits(self.k.journal, self.claim, STORE_WAIT), "journal.read"
        )
        delay = recheck_delay(waited, STORE_RECHECK_S)
        if delay is None:
            self.log.warning("engine.kernel.store_unavailable", waits=waited, outcome="failed")
            return await self.terminate(Fail(STORE_UNAVAILABLE), "failed")
        self.log.warning("engine.kernel.store_wait", wait=waited + 1, delayS=delay, errorType=type(exc).__name__)
        not_before = db_timestamp(self.store_time.now() + timedelta(seconds=delay))
        _ = await self.commit("yield", [], Yield(not_before, STORE_WAIT))
        return Disposition("yielded", STORE_WAIT)

    async def _commit_rejected(self, refused: CommitRejected) -> Disposition:
        """A commit the journal refuses for good: the run fails once, with nothing more journaled than its end.
        Released, the next attempt would run the engine's whole turn again to be refused the same way."""
        self.log.error("engine.kernel.commit_rejected", refusal=type(refused).__name__, reason=str(refused)[:200])
        try:
            return await self.terminate(Fail(COMMIT_REJECTED, str(refused)[:500] or None), "failed")
        except Exception as exc:
            # Nothing could be recorded: the lease runs out, and a run that keeps failing so is quarantined.
            self.log.error("engine.kernel.fail_not_journaled", exc=exc, errorType=COMMIT_REJECTED)
            return Disposition("lost", f"{COMMIT_REJECTED}; not journaled: {type(exc).__name__}")

    async def release(self, reason: str) -> Disposition:
        try:
            await self.k.journal.release(self.claim.token, reason=reason)
        except (JournalUnavailable, OwnershipLost) as exc:
            self.log.warning("engine.kernel.release_failed", reason=reason, errorType=type(exc).__name__)
            return Disposition("lost", reason)
        self.log.info("engine.kernel.release", reason=reason)
        return Disposition("released", reason)

    async def _reap(self, labels: Mapping[str, str]) -> None:
        broker = self.k.broker
        if broker is None:
            return
        try:
            reaped = await broker.reap(labels=labels, older_than_s=0)
        except Exception as exc:
            self.log.warning("engine.kernel.reap_failed", errorType=type(exc).__name__)
            return
        if reaped:
            self.log.info("engine.kernel.reaped", sandboxes=reaped)

    # ---- journal ----

    async def leased[T](self, call: Callable[[], Awaitable[T]], operation: str) -> T:
        return await while_leased(
            call,
            lease=self.lease,
            retry_s=self.k.journal_retry_s,
            sleep=self.k.sleep,
            on_retry=lambda retries: self.log.warning(
                "engine.kernel.retry", operation=operation, retries=retries, retryS=self.k.journal_retry_s
            ),
        )

    async def sync(self) -> None:
        async with self._lock:
            await self._sync_locked()

    async def _granted_since(self, reason: Mapping[str, JsonValue]) -> bool:
        """Whether a conversation grant the run's stream now holds answers an ask with ``reason`` (FLOWGRANT): only an
        ask that offered the grant, for its tool, destination and the data the run had read."""
        if not isinstance(self.ledger, GrantingLedger) or reason.get("conversationGrant") is not True:
            return False
        destination, tool_id, sensitivity = reason.get("destination"), reason.get("toolId"), reason.get("sensitivity")
        if not isinstance(destination, dict) or not isinstance(tool_id, str) or sensitivity not in SENSITIVITIES:
            return False
        key = cast("dict[str, JsonValue]", destination).get("key")
        if not isinstance(key, str):
            return False
        await self.sync()
        granted = self.ledger.grants.allows(tool_id, key, cast("Sensitivity", sensitivity))
        if granted:
            self.log.info("tool.approval_granted_elsewhere", toolId=tool_id, sensitivity=str(sensitivity))
        return granted

    async def _before_call(self) -> None:
        """While a conversation grant was offered and not yet answered, the session's stream, so a grant given while
        the engine waited answers its next call to the same place (UX2). What was shown before the call is journaled
        ahead of it by the call's own effect commit (``_effect_commit``)."""
        if isinstance(self.ledger, GrantingLedger) and self.ledger.grants.awaiting():
            await self.sync()

    async def _effect_commit(self, kind: str, events: list[NewEvent]) -> CommitReceipt:
        """A gateway call's audit, carrying the progress shown before it in the same commit: the call waits on its
        intent and outcome alone (PF-05: each commit is a journal transaction), and the lines keep their order."""
        receipt = await self.commit(kind, [*await self.sink.take(), *events])
        # Before the gateway answers the engine: its next model call must see what this call's result brought in.
        await self._record_level()
        return receipt

    async def _record_level(self) -> None:
        """The host's record of what the conversation holds once the ledger has seen the last commit (the model
        gateway reads it with the run's grant, GA-16); nothing when it has not changed."""
        if self._level_events is None:
            return
        events = list(self._level_events())
        if events:
            _ = await self.commit("recorded_fields", events)

    async def _sync_locked(self) -> None:
        events = await self.leased(
            lambda: self.k.journal.read(self.claim.stream_id, after_seq=self.state.seq), "journal.read"
        )
        self._fold(events)

    def _fold(self, events: Sequence[JournalEvent]) -> None:
        self.session_runs.apply(events)
        if self.ledger is not None:
            self.ledger.apply(events)
        self.state = fold_engine_run(events, run_id=self.claim.run_id, state=self.state)
        self.events.extend(e for e in events if e.run_id == self.claim.run_id)

    async def commit(
        self,
        kind: str,
        events: list[NewEvent],
        transition: Transition | None = None,
        *,
        command: str | None = None,
    ) -> CommitReceipt:
        async with self._lock:
            # A commit that names its command is its sender's batch, already within the journal's limit (the sink's).
            parts = [events] if command is not None else pack(events) or [[]]
            receipt: CommitReceipt | None = None
            for index, part in enumerate(parts):
                self.lease.check()
                self._commits += 1
                request = CommitRequest(
                    command or command_id(self.claim.attempt_id, kind, self._commits),
                    self.claim.token,
                    tuple(part),
                    transition if index == len(parts) - 1 else None,
                )
                try:
                    receipt = await self.leased(lambda request=request: self.k.journal.commit(request), "journal.commit")
                except CommitRejected as refused:
                    request = await self._sealed_retry(request, refused)
                    receipt = await self.leased(lambda request=request: self.k.journal.commit(request), "journal.commit")
                if receipt.events and receipt.events[0].seq == self.state.seq + 1:
                    self._fold(receipt.events)
                else:
                    await self._sync_locked()
            assert receipt is not None
            return receipt

    async def _sealed_retry(self, request: CommitRequest, refused: CommitRejected) -> CommitRequest:
        """The refused completion again, its child result stored sealed: the engine's finished work is kept in
        this attempt. Any other refusal is raised as it came."""
        transition = request.transition
        sealed = (
            await sealed_completion(transition, self.content.put_sealed) if isinstance(transition, Complete) else None
        )
        if sealed is None:
            raise refused
        self.log.warning("engine.kernel.commit_rejected", reason=str(refused)[:200], recovery="child_result_sealed")
        self._commits += 1
        return CommitRequest(
            command_id(self.claim.attempt_id, "complete", self._commits), request.token, request.events, sealed
        )


def parent_reader(
    store: WorkspaceStore, tenant: str, log: HarnessLog
) -> Callable[[WorkspaceManifest], Awaitable[Mapping[str, WorkspaceFile] | None]]:
    """The files of a saved revision's parent, for the save's diff. A parent the store cannot read fails the
    save: diffed against nothing, every file would be journaled as changed."""

    async def parent(manifest: WorkspaceManifest) -> Mapping[str, WorkspaceFile] | None:
        if manifest.parent_revision is None:
            return None
        try:
            return (await store.revision(ObjectScope(tenant), manifest.workspace_id, manifest.parent_revision)).files
        except Exception as exc:
            log.warning(
                "engine.workspace.parent_unreadable",
                workspaceId=manifest.workspace_id,
                revision=manifest.revision,
                parentRevision=manifest.parent_revision,
                errorType=type(exc).__name__,
            )
            raise

    return parent


def _decisions_note(decisions: Mapping[str, str], said: Mapping[str, str] | None = None) -> str:
    """Names each interaction with its decision: the engine's deferred results named them, and two held calls of
    one tool are told apart only by them (E2E3-F5). A denial's reason rides with it (AP-05), quoted as data."""
    said = said or {}
    approved = [ix for ix, d in decisions.items() if d == "approved"]
    refused = [ix for ix, d in decisions.items() if d != "approved"]
    named = "".join(
        f" {label}: {', '.join(ids)}." for label, ids in (("Approved", approved), ("Not approved", refused)) if ids
    )
    reasons = "".join(
        f" The person's reason for {ix}: {json.dumps(said[ix][:MAX_DENY_REASON_CHARS], ensure_ascii=False)}."
        for ix in refused
        if said.get(ix)
    )
    adapt = " Change your approach to fit their reasons." if reasons else ""
    # An approval's answer is the arguments the person approved instead (AP-06); the re-call runs those. A connect
    # wait is answered by connecting, never with arguments (G-12).
    edited = [ix for ix in approved if said.get(ix) and not ix.startswith("ix.connect.")]
    changed = (
        f" The person changed the arguments of {', '.join(edited)} before approving; it runs with their changes, "
        "and its result says what ran."
        if edited
        else ""
    )
    connects = [ix for ix in decisions if ix.startswith("ix.connect.")]
    if connects:
        # G-12: a connect wait is answered by connecting, not approving: say so, so the engine re-calls those tools.
        connected = [ix for ix in connects if decisions[ix] == "approved"]
        named += "".join(
            f" {label}: {', '.join(ids)}."
            for label, ids in (
                ("Connected", connected),
                ("Not connected", [ix for ix in connects if ix not in connected]),
            )
            if ids
        )
    return (
        "The approvals you were waiting for are decided: "
        f"{len(approved)} approved, {len(refused)} not approved.{named}{reasons}{changed} Call each approved tool "
        f"again with the same arguments to run it; do not call the others again.{adapt}"
    )


class _Settled(NamedTuple):
    """A settled run: its outcome, its ledger, whether its answer holds a result (a parent takes the turn back when
    not) and the presenter's framing of its answer (WP-89: the chat shows the prose and words the rest itself)."""

    outcome: RunOutcome
    ledger: OutcomeLedger
    answered: bool
    framed: Framed | None = None


def _kept(ledger: OutcomeLedger) -> bool:
    """Whether the run's work was saved for a "continue": its handoff, and no save refused after it."""
    return HANDOFF_PATH in ledger.notes and ledger.save_refused is None


def _owed_by_size(deliverable: Deliverable, files: Mapping[str, WorkspaceFile]) -> str | None:
    """The requested file a completed turn leaves unsettled, by size alone (the controller reads no text): the file
    the request names while neither out/ nor a file of that name holds more than a placeholder (WP-70)."""
    return OutcomeLedger(list(workspace_artifacts(files, 0, deliverable))).owed_at_end(deliverable)


def _code(refused: Exception) -> str:
    for name in ("reason_code", "error_type", "code"):
        value = getattr(refused, name, None)
        if isinstance(value, str) and value:
            return value
    return ENGINE_FAILED


def _terminal(status: str) -> DispositionStatus:
    return cast("DispositionStatus", status) if status in ("completed", "failed", "cancelled", "quarantined") else "failed"


__all__ = [
    "BUDGET_EXHAUSTED",
    "ENGINE",
    "ENGINE_FAILED",
    "ENGINE_INPUTS",
    "ENGINE_PROFILE_MISMATCH",
    "ENGINE_PROFILE_MISSING",
    "EXECUTOR_ENGINE",
    "INTERRUPTED_NOTE",
    "SAVED_NOTE",
    "EngineKernel",
]
