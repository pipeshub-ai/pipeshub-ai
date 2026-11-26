"""What the durable kernel needs from a model, the tools and the run's policies."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from harness_core.effects.types import (
    ApprovalDecision,
    ApprovalPolicy,
    EffectUnknown,
    ToolContext,
    ToolExecution,
    allow_all,
)
from harness_core.invoker.ports import ToolCatalog
from harness_core.kernel.core.responses import StopReason, TokenUsage
from harness_core.kernel.durable.harness_channel import HarnessInstruction

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.budget.wrap_up import WrapUpSize
    from harness_core.effects.types import ToolEffect
    from harness_core.journal.types import JournalEvent, NewEvent
    from harness_core.kernel.core.messages import (
        AssistantMessage,
        Message,
        Part,
        ToolCall,
    )
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
    from harness_core.kernel.core.turn_limit import CutShort
    from harness_core.kernel.fold import RunState
    from harness_core.models.stream_watch import CallTiming

DeltaKind = Literal["text_delta", "reasoning_delta", "tool_call"]


@dataclass(frozen=True)
class ModelCall:
    messages: list[Message]
    tools: list[ToolSchema] | None = None
    system: str | None = None
    # The run's slots as the turn starts. A key a shaper adds or changes is journaled with the turn's response
    # (``loop.state_saved``), as a turn hook's are: what the request showed, recorded in the turn it was sent.
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # Where the run's own input message is in ``messages`` (a shaper may
    # present the input differently, e.g. as a rewritten goal).
    input_index: int | None = None
    # Set by the context compiler (P4.1): the system band and the pinned band
    # as separate blocks, the message indices that end a cacheable band, and
    # the digest of the compiled bands.
    system_blocks: list[str] | None = None
    cache_marks: tuple[int, ...] | None = None
    context_digest: str | None = None
    # Set by a host whose tool list is fixed for the session (R4, ``harness_core.kernel.session_tools``): the
    # session's list under this key replaces ``tools``, and the first request of the session journals it.
    tool_list_key: str | None = None
    # The answer-only turn after max_turns asks for no tool calls while the tools are still sent (main #4379):
    # providers reject tool-call history without the definitions it refers to.
    tool_choice: ToolChoice | None = None
    # The answer-only turn after the spending limit is bounded by its wrap-up reserve (BUDGETEND).
    max_output_tokens: int | None = None

    def with_system_block(self, block: str) -> ModelCall:
        """This call with ``block`` as its last system block (ADR-RUN-01 D5: the turn's harness instructions). Both
        system forms carry it, as each transport reads one or the other; the context digest then names the request
        actually sent."""
        if not block:
            return self
        digest = (
            "sha256:" + sha256(f"{self.context_digest}\n{block}".encode()).hexdigest()
            if self.context_digest is not None
            else None
        )
        return replace(
            self,
            system=f"{self.system}\n\n{block}" if self.system else block,
            system_blocks=[*self.system_blocks, block] if self.system_blocks else None,
            context_digest=digest,
        )


@dataclass(frozen=True)
class SampledTurn:
    """One model response. ``raw`` is the provider-native state (thinking signatures,
    encrypted reasoning items) journaled as ``rawRef`` so a resume never depends on
    server-side conversation state (REPORT 6.4)."""

    message: AssistantMessage
    usage: TokenUsage = field(default_factory=TokenUsage)
    stop_reason: StopReason = StopReason.END_TURN
    provider: str = "unknown"
    transport: str = "unknown"
    model: str = "unknown"
    raw: bytes | None = None
    # How the call progressed (P4.10); logged on ``model.response``, never journaled.
    timing: CallTiming | None = None


class ModelBackoff(Exception):
    """The provider asked us to wait (429 / Retry-After); ``provider`` False: the budget ledger did, whose outage
    is not the provider's and is not bounded by its waits (``provider_wait``)."""

    def __init__(self, retry_after_s: float, *, provider: bool = True) -> None:
        super().__init__(f"retry after {retry_after_s:.3f}s")
        self.retry_after_s = retry_after_s
        self.provider = provider


# ``ModelFailed.error_type`` when the provider refuses a request as larger than its window: with the
# context compiler on, the kernel reduces the request and sends it again (P4.9, legacy L8's job).
REQUEST_TOO_LARGE = "request_too_large"


class ModelFailed(Exception):
    """A non-retryable model error; the run fails."""

    def __init__(self, error_type: str, message: str = "", *, budget_scope: str | None = None) -> None:
        super().__init__(message or error_type)
        self.error_type = error_type
        # A budget stop's shared budget scope (WP-51), opaque here; None for any other failure.
        self.budget_scope = budget_scope


class DurableModelPort(Protocol):
    async def sample(
        self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]
    ) -> SampledTurn: ...

    def restore(
        self, raw: bytes, *, provider: str, transport: str
    ) -> AssistantMessage | None:
        """Rebuild the provider-native message from ``raw``; ``None`` when this port
        cannot use it (a different provider or transport: provider-bound blocks are
        dropped deliberately and the normalized message is used)."""
        ...


class DurableToolPort(ToolCatalog, Protocol):
    """The run's ``ToolCatalog`` (the Invoker runs its calls) plus what the
    kernel shows the model about tools it does not have."""

    def describe(self, name: str) -> ToolEffect | None:
        """Effect metadata, or ``None`` for a tool this run does not have."""
        ...

    def unknown(self, name: str) -> str:
        """The error outcome of a call to a tool this run does not have."""
        ...


@runtime_checkable
class TerminalTools(Protocol):
    """A tool port that knows which of its tools end the run (``final_answer``): the answer-only turn after
    ``maxTurns`` keeps only those calls, as legacy's does."""

    def ends_run(self, name: str) -> bool: ...


@runtime_checkable
class WithheldTools(Protocol):
    """A tool port that knows tools its run is never given and why (a sub-agent's coordination tools, ADR-RUN-04):
    a call to one, even one an earlier list of the session showed, is told that reason."""

    def withheld_reason(self, name: str) -> str | None: ...


class PreRunStep(Protocol):
    """Runs once per run before the first model call (intent routing, goal rewrite).

    Its result is journaled as slot ``pre_run.<key>``; a resumed run reads it back
    instead of running the step again.
    """

    @property
    def key(self) -> str: ...

    async def run(self, input_text: str) -> JsonValue: ...


class PostRunStep(Protocol):
    """Runs at most once per run, as it ends (e.g. learning from the run).

    Before it runs, ``post_run.<key> = {"state": "started"}`` is committed; its
    result rides the run's terminal commit as ``{"state": "done", "result": …}``
    (or ``failed``). A later attempt that finds the slot does not run the step
    again, even when only ``started`` was committed: the step's writes are not
    journaled, so at most once is the only promise a re-drive can keep. A step
    that raises never changes how the run ends.
    """

    @property
    def key(self) -> str: ...

    async def run(self, ending: RunEnding) -> JsonValue: ...


class AfterRunStep(Protocol):
    """Work that follows a run's answer without delaying it (UX5: suggested next questions).

    ``prepare`` runs while the attempt still holds its lease, before the terminal commit (e.g. a short budget
    reservation, which only a leased attempt may make); it is cheap and returns ``None`` when there is nothing to
    do. Once the terminal commit has landed, ``run`` is started in the background and the run ends at once; if the
    commit did not land (Node refused it for queued input), ``discard`` undoes the preparation. Neither may change
    how the run ends: they journal nothing, and a failure is logged and dropped.
    """

    @property
    def key(self) -> str: ...

    async def prepare(self, ending: RunEnding) -> object | None: ...

    async def run(self, prepared: object) -> None: ...

    async def discard(self, prepared: object) -> None: ...


class PreRunGate(Protocol):
    """Decides from the pre-run steps' results that the run asks the user
    before any model call (a request too ambiguous to act on): the run then
    completes ``needs_input`` with what the presenter shows."""

    def needs_input(self, slots: Mapping[str, JsonValue]) -> bool: ...


class CapabilityCheck(Protocol):
    """Digests of the capabilities a run observes when it is built (e.g. live MCP
    manifests). The first attempt journals them; a later attempt that observes
    something else suspends with ``capability_lost`` instead of running on it."""

    async def observe(self) -> Mapping[str, JsonValue]: ...

    async def withhold(self, marks: Mapping[str, JsonValue]) -> None:
        """Build this attempt without the capabilities ``marks`` names, which the first attempt recorded as withheld
        (``capability.returned``): their tools are not offered or callable, and a later ``observe`` reports each with
        its recorded mark. Asked before the attempt's first model call (WP79-FU-5)."""
        ...


RunEndingOutcome = Literal["answered", "needs_input", "stopped", "error"]


@dataclass(frozen=True)
class RunEnding:
    """How a run ends, for the product's answer presentation.

    ``texts`` are the run's assistant message texts in order (one per model
    turn); ``error_type`` names the failure when ``outcome`` is ``error``;
    ``events`` are the run's journaled events, so a presenter can rebuild
    what earlier attempts presented instead of trusting process memory;
    ``answer`` is the answer when it is not the last model turn's text (a
    turn budget's degraded answer, a terminal tool's answer, the text a
    director ended the run with) and ``confidence`` the confidence a
    terminal tool reported with it.
    """

    outcome: RunEndingOutcome
    texts: tuple[str, ...] = ()
    error_type: str | None = None
    events: tuple[JournalEvent, ...] = ()
    answer: str | None = None
    confidence: str | None = None
    # The tool calls the run's model made, in order (``PostRunStep`` input).
    tool_calls: tuple[ToolCall, ...] = ()
    # A limit cut the run's work short; it answered from what it had gathered (BUDGETEND).
    truncated_by: CutShort | None = None
    # With ``truncated_by`` ``budget``: which shared budget ran out (opaque here); None: the run's own.
    budget_scope: str | None = None


class RunPresenter(Protocol):
    """How a run is shown (REPORT 8): extension events journaled next to the
    facts they describe. Presentation is never truth: a presenter that fails
    costs the projection its detail, never the run."""

    def pre_run(self, slots: Mapping[str, JsonValue]) -> list[NewEvent]:
        """Journaled with the pre-run commit (e.g. the goal a pre-run step settled on)."""
        ...

    def response(self, message: AssistantMessage) -> list[NewEvent]:
        """Journaled with the ``model.response`` (its text and tool calls, ids assigned)."""
        ...

    async def finalize(self, ending: RunEnding) -> list[NewEvent]:
        """Journaled with the run's terminal commit."""
        ...


class RunRestore(Protocol):
    """Rebuilds product state a run keeps in process (e.g. the sources its
    citations are built from) from the run's journaled events, before an
    attempt runs anything."""

    async def restore(self, events: Sequence[JournalEvent]) -> None: ...


@dataclass(frozen=True)
class SteeringInput:
    """A steering message that names stored attachments or records (by reference,
    authorized by Node at admission): ``attachments`` in their wire shape."""

    message_seq: int
    text: str
    attachments: tuple[Mapping[str, JsonValue], ...] = ()
    record_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderedInput:
    """The user message's parts, and product events journaled with its
    consumption (e.g. the citation sources its attachments added)."""

    parts: list[Part]
    events: list[NewEvent] = field(default_factory=list["NewEvent"])


class InputRenderer(Protocol):
    """Renders a steering message with references as the user message a new turn
    with them would give the model. Called at the boundary that consumes it; the
    parts are journaled with the consumption, so no later attempt renders it again."""

    async def render(self, message: SteeringInput) -> RenderedInput: ...


class TurnShaper(Protocol):
    """Shapes each model call from what the run journaled: the system prompt
    and tools its pre-run steps decided, the context shaping of its loop
    strategy. A function of the journal (``state``), so a resumed attempt sends
    what the first one would have."""

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall: ...

    async def enter(self, turn: int, state: RunState) -> None:
        """Before ``turn``'s tools run: open the turn's scope from the journal
        when this attempt did not shape the turn (it resumed at dispatch)."""
        ...


class ResponseReview(Protocol):
    """Reviews a response that called no tool before the run answers with it:
    a recovery message (an empty or truncated response nudged to continue)
    makes the kernel journal it and call the model again instead."""

    async def review(
        self, message: AssistantMessage, turn: int, state: RunState
    ) -> str | None: ...


class ImportedHistory(Protocol):
    """A history the session imported from before its durable runs
    (``session.imported``), as the messages the product would have given the
    model for it. ``transcript`` is the imported bytes as Node journaled them."""

    async def messages(self, transcript: bytes) -> list[Message]: ...


class TurnHook(Protocol):
    """Loop-strategy phase state: slot updates journaled with the next model response."""

    def __call__(self, state: RunState) -> Mapping[str, JsonValue] | None: ...


@dataclass(frozen=True)
class TurnResult:
    """One journaled result of the turn before a boundary, as a director reads it."""

    name: str
    call_id: str
    status: str
    content: JsonValue


@dataclass(frozen=True)
class TurnPlan:
    """What a loop strategy does at the boundary before a turn.

    ``inject``: user messages it adds before the turn's request (legacy's
    ``inject_user_message``). ``synthetic``: the turn's response when the
    strategy takes the turn itself instead of the model (its calls run like a
    model's; its text is never presented). ``slots``: its phase state.
    ``events``: presentation events journaled with the boundary (a plan's steps moving, UXR-B1).
    ``cut_short``: the turn is the run's last, its answer flagged as cut short by that limit (BUDGETEND): a model
    turn is then the answer-only one (no tools, on the wrap-up reserve for ``budget``), a synthetic one is the
    answer as it is (ENGBUDGET: a hand-off whose engine stopped early).
    ``harness``: the strategy's instructions for this turn, sent in the system channel as the harness's, never as a
    message of the person's turn (ADR-RUN-01 D5, ``harness_channel``).
    """

    inject: tuple[str, ...] = ()
    harness: tuple[HarnessInstruction, ...] = ()
    synthetic: AssistantMessage | None = None
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    events: tuple[NewEvent, ...] = ()
    cut_short: CutShort | None = None


class ChildResultShaper(Protocol):
    """A child run's result document as its product completes it (K-L): what
    its definition does to an answer, and product data its parent adopts on
    the join (namespaced keys, e.g. citation sources)."""

    async def shape(
        self, document: dict[str, JsonValue], outcome: RunEndingOutcome
    ) -> dict[str, JsonValue]: ...


class WrapUpPort(Protocol):
    """The run's wrap-up reserve (BUDGETEND, ``harness_core.budget.wrap_up``): budget held back so the answer-only
    turn after the spending limit is paid for. ``hold`` before the working phase spends, ``arm`` before the
    answer-only turn's call (it then draws on the reserve), ``release`` when the attempt ends without it."""

    size: WrapUpSize

    @property
    def held(self) -> bool: ...

    async def hold(self) -> bool: ...

    def arm(self) -> bool: ...

    async def release(self) -> None: ...


class LoopDirector(Protocol):
    """A loop strategy's phases on the durable kernel (deep mode's ``OrchestratorLoop``).

    Asked before every model call; a pure function of the run's slots and the
    previous turn's results, so a re-claimed attempt that finds no committed
    answer gets the same one. The kernel commits the plan with the turn.
    """

    def before(
        self, turn: int, state: RunState, previous: Sequence[TurnResult]
    ) -> TurnPlan: ...


__all__ = [
    "ApprovalDecision",
    "ApprovalPolicy",
    "CapabilityCheck",
    "ChildResultShaper",
    "DeltaKind",
    "DurableModelPort",
    "DurableToolPort",
    "EffectUnknown",
    "ModelBackoff",
    "ModelCall",
    "REQUEST_TOO_LARGE",
    "ModelFailed",
    "InputRenderer",
    "LoopDirector",
    "PreRunGate",
    "PostRunStep",
    "AfterRunStep",
    "PreRunStep",
    "ResponseReview",
    "RenderedInput",
    "RunRestore",
    "RunEnding",
    "RunEndingOutcome",
    "RunPresenter",
    "SampledTurn",
    "SteeringInput",
    "TerminalTools",
    "WithheldTools",
    "ToolContext",
    "ToolExecution",
    "TurnHook",
    "TurnPlan",
    "TurnResult",
    "TurnShaper",
    "WrapUpPort",
    "allow_all",
]
