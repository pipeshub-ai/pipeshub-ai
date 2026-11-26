"""What the durable engine kernel asks its host for (ARCHITECTURE §4.1, E1).

The kernel owns the journal: the attempt's fenced commit, the effect log, the
approval hold and the event sink are its. The host assembles what a run is
granted (``EngineRuntimeAssembler``: the profile, the tool catalog as a
``RunToolEndpoint`` over the kernel's ``EffectRunner`` pieces, a controller
bound to the attempt, the artifact publisher over the kernel's publication
log) and how engine progress is shown (``EngineRunPresenter``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from harness_core.sandbox.types import ResourceCaps

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import timedelta

    from pydantic import JsonValue

    from harness_core.artifacts.ports import ArtifactPublisher
    from harness_core.artifacts.types import ArtifactCard
    from harness_core.budget.run_budget import PhasePlan
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.effects.log import EffectLog
    from harness_core.effects.runner import ApprovalHold
    from harness_core.engines.controller import EngineController
    from harness_core.engines.durable.tool_pin import EngineToolPin
    from harness_core.engines.events import EngineEvent, NativeUnknown
    from harness_core.engines.git_checkout import GitCheckout
    from harness_core.engines.native import TranscriptPart
    from harness_core.engines.ports import EngineEventSink, SourceLedger
    from harness_core.engines.signin import PlanSignIn
    from harness_core.engines.types import EngineInput, EngineProfile
    from harness_core.engines.usage import RunUsageLedger
    from harness_core.gates.verify import VerifyPorts
    from harness_core.gateway.asking import AskedQuestion
    from harness_core.gateway.ports import ToolEndpoint
    from harness_core.gateway.types import GatewayBinding, McpResult
    from harness_core.ids import IdSource
    from harness_core.interactions.connect import ConnectGate
    from harness_core.interactions.connect_wait import BridgeConnectWait
    from harness_core.interactions.ports import RunBinding
    from harness_core.journal.ports import ContentStorePort
    from harness_core.journal.types import (
        Claim,
        InputArtifactRef,
        JournalEvent,
        NewEvent,
    )
    from harness_core.kernel.core.turn_limit import CutShort
    from harness_core.kernel.fold import RunState
    from harness_core.sandbox.types import EgressDestination
    from harness_core.skills.types import SkillPin
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import WorkspaceManifest


@dataclass(frozen=True)
class EngineAttempt:
    """The kernel's half of one attempt, handed to the assembler.

    ``effect_log`` and ``hold`` are bound to the attempt's fenced commit and the
    run's interactions; ``guard`` stops an effect once the lease is gone and
    ``propagate`` are the errors that must stop the run rather than become an
    outcome. ``append`` journals product events (artifact publications) through the
    same fenced commit and ``run_events`` reads the run's events back, which is all
    an event-backed ``PublicationLog`` needs; ``stamp`` places a presented line in
    the running turn.
    """

    claim: Claim
    profile: EngineProfile
    effect_log: EffectLog
    hold: ApprovalHold
    guard: Callable[[], None]
    propagate: tuple[type[BaseException], ...]
    append: Callable[[Sequence[NewEvent]], Awaitable[None]]
    run_events: Callable[[], Awaitable[Sequence[JournalEvent]]]
    stamp: Callable[[], ProgressStamp]
    ids: IdSource
    # The journal-bound sink the attempt's controller must report to.
    sink: EngineEventSink
    # The run's tool calls as folded from its journal, as they stand now (a run gate decides from it, G-04).
    run_state: Callable[[], RunState] | None = None
    # Binds a gate to the run's interactions, so a call of an unconnected item waits for its owner (G-12);
    # ``None`` when the claim's control plane does not take connect waits.
    connect_wait: Callable[[ConnectGate], BridgeConnectWait] | None = None


def _no_secrets() -> tuple[str, ...]:
    return ()


@dataclass
class AssembledEngineRun:
    """What one attempt runs with. ``tools`` is the run's ``RunToolEndpoint``; ``controller``
    is built for this attempt (its workspace writer lease bound to it)."""

    controller: EngineController
    tools: ToolEndpoint
    tool_binding: GatewayBinding
    grant_size: int
    artifacts: ArtifactPublisher
    content: ContentStorePort
    # tool id -> idempotent, for settling the effects a dead attempt left without an outcome.
    idempotent: Mapping[str, bool] = field(default_factory=dict[str, bool])
    # Where a saved revision's parent is read, so ``workspace.committed`` names what changed.
    workspaces: WorkspaceStore | None = None
    # How this attempt's progress is shown; ``None``: the kernel's own presenter.
    presenter: EngineRunPresenter | None = None
    skills: tuple[SkillPin, ...] = ()
    instructions: str = ""
    labels: Mapping[str, str] = field(default_factory=dict[str, str])
    close: Callable[[], Awaitable[None]] | None = None
    # Resolves the artifacts the run's input names (E1b); ``None``: this host takes none, and a run naming
    # some is refused, never run without them.
    inputs: EngineInputSource | None = None
    # Where the run's published files may have drawn their content from (P2.5a); the kernel feeds it the
    # session's stream. ``None``: their sources stay unknown (hidden from other viewers).
    ledger: SourceLedger | None = None
    # The attempt's own token values as they are now: a sandbox snapshot holding one is refused (P5.9b).
    secrets: Callable[[], tuple[str, ...]] = _no_secrets
    # Where the first turn's pinned input bytes are read for the task's workspace note (G2C-2); ``None``: a run
    # with inputs starts without the note.
    objects: BinaryObjectStore | None = None
    # What a child run tells its parent beyond its answer (CTL-6: the files it published); ``None``: the answer.
    child_result: Callable[[dict[str, JsonValue], Sequence[JournalEvent]], dict[str, JsonValue]] | None = None
    # The run is pinned on its actor's own plan (SIGNIN): its engine signs in inside each turn's sandbox.
    own_login: PlanSignIn | None = None
    # The run's wrap-up (ENGBUDGET): budget held back for one answer when a limit stops the engine before it wrote
    # one. ``None``: an unmetered run, whose cut-short answer is the no-model one.
    wrap_up: EngineWrapUp | None = None
    # Where each turn's usage is read: the run's gateway ledger account (OWD-8). ``None``: an unmetered run, whose
    # usage is the engine's own report.
    usage: RunUsageLedger | None = None
    # Events recording what the run's conversation now holds (an edition's run fields, which the model gateway reads
    # with the run's grant), asked after the ledger has seen the session's stream and after each gateway call's
    # commit; empty when nothing changed. ``None``: the host records nothing.
    level_events: Callable[[], Sequence[NewEvent]] | None = None
    # The session's one gateway tool list for this engine (OWD-7, TG-14); ``None``: each run serves its own.
    tool_pin: EngineToolPin | None = None
    # What this attempt was granted, as the loop's ``CapabilityCheck`` observes it (WP-84): the kernel journals the
    # first attempt's, and a later attempt that finds a tool its MCP server withdrew fails at once naming the server
    # (WP-79 D3). ``None``: the host observes nothing.
    capabilities: Callable[[], Awaitable[Mapping[str, JsonValue]]] | None = None
    # ``CapabilityCheck.withhold`` for the engine host: the attempt serves none of the tools the first attempt was
    # built without and that came back since (WP79-FU-5). ``None``: the host withholds nothing.
    withhold: Callable[[Mapping[str, JsonValue]], Awaitable[None]] | None = None
    # How long after it was taken a sandbox snapshot may be restored (OWD-12, the run's ``snapshots.ttlDays``); an
    # older one is not restored and the turn starts cold. ``None``: any age.
    snapshot_ttl: timedelta | None = None
    # The repository the run works on (GIT-FLOW): cloned in a setup phase, pushed only to its session branch.
    checkout: GitCheckout | None = None
    # What the run's verify gate (G-05) may look at; ``None``: every check that needs a port fails closed.
    verify: VerifyPorts | None = None
    # The run's allowed websites (WP-63), asked at each turn's start so a site an admin removed, or one the flow rule
    # now holds, is not in that turn's sandbox. ``None``: the run reaches no site.
    sites: Callable[[], Awaitable[tuple[EgressDestination, ...]]] | None = None
    # SEC-A-02: brings the live sandbox's egress in line with what the conversation holds now (the flow rule, the
    # classification level), after each gateway call and before its result reaches the engine; it raises when the
    # proxy could not be told, and that call's result is withheld. ``None``: nothing to narrow mid-turn.
    egress_flow: Callable[[], Awaitable[None]] | None = None
    # C4.10 ``toolResults.maxChars``: the most of one tool result the engine reads inline; ``None``: the release's.
    result_cap_chars: int | None = None
    # C4.10 ``sandbox.*``: what the run's org or agent lowered the engine template's resources to.
    resource_caps: ResourceCaps = ResourceCaps()
    # G-52: the line naming the person's answer language, from the run's pinned locale; the kernel puts it at the tail
    # of the run's first task, never in the instructions a session opens with. "": none.
    answer_language: str = ""
    # Facts about the task the app found in it (the repositories it names, R41-CLONE), said at the first task's tail.
    task_facts: str = ""


@dataclass(frozen=True, slots=True)
class GatheredResult:
    """One tool result the run got, as the wrap-up answer reads it; ``source``: the page it came from (WP-70), or
    for the engine's own notes the file they are in."""

    tool: str
    text: str
    source: str | None = None


class EngineWrapUp(Protocol):
    """BUDGETEND's wrap-up for an engine run. ``hold`` reserves the answer's budget before the engine spends (its
    gateway calls then stop short of it); ``answer`` writes the answer from the run's request and the tool results
    it gathered, on that reserve, when a limit stopped the engine before it wrote one (``None`` when it cannot);
    ``earlier`` is the partial result an earlier cut-short run of the task left, which the answer extends
    (SPENDCONT); ``release`` returns the reserve unused when the attempt ends."""

    async def hold(self) -> bool: ...

    async def answer(
        self, request: str, gathered: Sequence[GatheredResult], reason: CutShort, *, earlier: str | None = None
    ) -> str | None: ...

    async def release(self) -> None: ...


class EngineInputSource(Protocol):
    """The artifacts a run's input names, as files its first turn starts with (E1b, EV2-26).

    Bound to the run's actor: each artifact must be of the run's session (``binding.scope``) and readable by
    that actor. The bytes are stored in the session's object scope, so the workspace can hold them.
    """

    async def resolve(self, binding: RunBinding, refs: Sequence[InputArtifactRef]) -> tuple[EngineInput, ...]:
        """In ``refs`` order, one input per artifact (a repeated ref once).

        ``EngineInputUnavailable`` for an artifact the run cannot take; ``EngineInputConflict`` when two
        would be one file under ``in/``.
        """
        ...


class EngineRuntimeAssembler(Protocol):
    """Builds exactly what the pinned spec grants one attempt of an engine run.

    Raises ``RunRefused`` (or a typed engine refusal) when the run can never run here.
    """

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun: ...


@dataclass
class ProgressStamp:
    """Where a presented line sits: the run's task, the turn, and the next sequence number."""

    task_id: str
    turn_id: str
    allocate: Callable[[], int]


class EngineRunPresenter(Protocol):
    """How an engine run shows progress, as non-critical journal events the projector renders.

    Every method returns the events to journal (possibly none); none raises for
    content it cannot show, and none is on the execution path's outcome.
    """

    def started(self, stamp: ProgressStamp, goal: str) -> list[NewEvent]: ...

    def event(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]: ...

    async def saved(self, stamp: ProgressStamp, manifest: WorkspaceManifest, reason: CommitReason) -> list[NewEvent]: ...

    def artifact(self, stamp: ProgressStamp, card: ArtifactCard) -> list[NewEvent]: ...

    def approval(
        self, stamp: ProgressStamp, interaction_id: str, tool: str, arguments: Mapping[str, JsonValue]
    ) -> list[NewEvent]: ...

    def finished(
        self,
        stamp: ProgressStamp,
        *,
        success: bool,
        reason: str | None,
        output: str,
        truncated_by: str | None = None,
        budget_scope: str | None = None,
        journal: Sequence[JournalEvent] = (),
    ) -> list[NewEvent]:
        """The run's ending. ``journal``: the run's events so far, for an answer that says what the run tried and
        could not do (ADR-RUN-03 D5); empty when it failed."""
        ...


@runtime_checkable
class ShowsCommands(Protocol):
    """An optional presenter facet (UX5): the engine's shell commands on the activity bus as they run, and their
    records to journal. Awaited for every command event, after ``event``."""

    async def command(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]: ...


@runtime_checkable
class ShowsToolResults(Protocol):
    """An optional presenter facet: what a gateway tool returned, shown on the step the engine started for it.
    The engine reports a tool's start and end only; its result passes through the run's endpoint."""

    def tool_result(
        self, stamp: ProgressStamp, tool: str, arguments: Mapping[str, JsonValue], result: McpResult
    ) -> list[NewEvent]: ...


@runtime_checkable
class AsksQuestions(Protocol):
    """An optional presenter facet (C3.70): the question an engine asked through ``ask_user``, shown as the chat's
    question card, and the run's ending while it waits for the answer (in place of ``finished``)."""

    def question(self, stamp: ProgressStamp, asked: AskedQuestion) -> list[NewEvent]: ...

    def awaiting_answer(self, stamp: ProgressStamp, output: str) -> list[NewEvent]: ...


@runtime_checkable
class LinksFiles(Protocol):
    """An optional presenter facet (MR-18): the engine's text as the person may read it, its links to the sandbox's
    own files (``sandbox:``, ``file://``, an absolute workspace path) named by the files the run published. The kernel
    applies it once, to the final text it journals (``message.completed``), ends the run with and hands a parent."""

    def linked(self, text: str) -> str: ...


@runtime_checkable
class ShowsTimePlan(Protocol):
    """An optional presenter facet (ADR-RUN-01 D4): the turn's time plan, shown to the person as the turn starts:
    when it starts writing at the latest and when its work must be finished."""

    def planned(self, stamp: ProgressStamp, plan: PhasePlan) -> list[NewEvent]: ...


@runtime_checkable
class KeepsNative(Protocol):
    """An optional presenter facet (C2.39, C2.40): how the host journals what an engine sent natively, its
    bytes sealed in the run's content store. Kept for audit and re-parsing (UP-07), never shown."""

    def native_unknown(self, stamp: ProgressStamp, event: NativeUnknown, content: ContentRef) -> list[NewEvent]: ...

    def native_transcript(self, stamp: ProgressStamp, part: TranscriptPart, content: ContentRef) -> list[NewEvent]: ...


class SilentPresenter:
    """A host that shows nothing beyond the core events."""

    def started(self, stamp: ProgressStamp, goal: str) -> list[NewEvent]:
        return []

    def event(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]:
        return []

    async def saved(self, stamp: ProgressStamp, manifest: WorkspaceManifest, reason: CommitReason) -> list[NewEvent]:
        return []

    def artifact(self, stamp: ProgressStamp, card: ArtifactCard) -> list[NewEvent]:
        return []

    def approval(
        self, stamp: ProgressStamp, interaction_id: str, tool: str, arguments: Mapping[str, JsonValue]
    ) -> list[NewEvent]:
        return []

    def finished(
        self,
        stamp: ProgressStamp,
        *,
        success: bool,
        reason: str | None,
        output: str,
        truncated_by: str | None = None,
        budget_scope: str | None = None,
        journal: Sequence[JournalEvent] = (),
    ) -> list[NewEvent]:
        return []


__all__ = [
    "AsksQuestions",
    "AssembledEngineRun",
    "EngineAttempt",
    "EngineInputSource",
    "EngineRunPresenter",
    "EngineRuntimeAssembler",
    "EngineWrapUp",
    "GatheredResult",
    "KeepsNative",
    "LinksFiles",
    "ProgressStamp",
    "ShowsCommands",
    "ShowsToolResults",
    "ShowsTimePlan",
    "SilentPresenter",
]
