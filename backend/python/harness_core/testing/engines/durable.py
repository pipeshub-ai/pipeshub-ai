"""An in-memory world for durable engine runs (E1): ``InMemoryJournal`` + ``RunDriver`` + ``EngineKernel``
over ``FakeEngine`` in a fake sandbox, the run's real ``RunToolEndpoint``, the in-memory workspace, checkpoint
and artifact stores.

Every attempt gets fresh workers' objects (a new ``RunDriver`` and kernel, a new controller), while the
stores a deployment keeps across workers (journal, content, workspaces, objects, artifacts, broker,
tool side effects) are the world's. ``effects`` counts what the run's tools did in the world, by
``(tool, args digest)``: a duplicate external effect is a count above one.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override

from harness_core import failpoints
from harness_core.access.types import AccessRouteRef, PinnedRoute, route_digest
from harness_core.artifacts.memory import InMemoryArtifactStore, InMemoryPublicationLog
from harness_core.artifacts.publisher import WorkspaceArtifactPublisher
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.common import ContentRef
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.effects import EffectRunner, ToolExecution, args_digest
from harness_core.engines.checkpoints import ObjectStoreEngineCheckpoints
from harness_core.engines.controller import EngineController, EngineControllerConfig
from harness_core.engines.durable.inputs import MAX_INPUT_BYTES, StoreInputSource
from harness_core.engines.durable.kernel import ENGINE, EngineKernel
from harness_core.engines.durable.ports import AssembledEngineRun, ProgressStamp
from harness_core.engines.durable.tool_pin import EngineToolPin
from harness_core.engines.events import EngineError, EngineEventMeta, TurnCompleted
from harness_core.engines.outcome.ledger import OUTCOME_RECORDED, OUTCOME_SETTLED
from harness_core.engines.outcome.presenter import Framed
from harness_core.engines.policy import engine_default_approvals, profile_rules
from harness_core.engines.profiles import profile_digest, profile_json
from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.engines.snapshots import ObjectStoreSandboxSnapshots, SnapshotPolicy
from harness_core.engines.types import EngineMode, EngineProfile, RunLimits
from harness_core.failpoints import Arm, Kill9, Nth
from harness_core.gates.gateway import gated_endpoint
from harness_core.gateway import RunToolEndpoint
from harness_core.gateway.asking import asks_person
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.gateway.types import McpResult, RunAttemptBinding, grant_digest
from harness_core.interactions.memory import InMemoryInteractionWakeup
from harness_core.invoker import CatalogTool, InMemoryToolCatalog, InvocationScope
from harness_core.journal.memory import InMemoryContentStore, InMemoryJournal
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.logs import HarnessLog
from harness_core.sandbox.types import RuntimeMode
from harness_core.skills.builder import StoredSkillBundleBuilder
from harness_core.skills.materializer import TarSkillMaterializer
from harness_core.skills.memory import InMemorySkillContentSource
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.fake_engine import FAKE_VERSION, FakeEngineAdapter
from harness_core.testing.engines.gateway import ConformanceGateway, GrantedTool
from harness_core.testing.engines.tool_host import InMemoryToolGatewayHost
from harness_core.testing.failpoints import armed
from harness_core.testing.ids import SeededIds
from harness_core.testing.sandbox.egress import AttemptTokenSource, FakeEgressProxy
from harness_core.testing.sandbox.fake import FakeSandboxBroker, FakeSandboxHandle
from harness_core.workspace.forks import session_scope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
    InMemoryWorkspaceWriters,
)
from harness_core.workspace.sync.strategies import SyncStrategies
from harness_core.workspace.sync.tar_sync import TarWorkspaceSync
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
    from datetime import timedelta

    from pydantic import BaseModel, JsonValue

    from harness_core.artifacts.types import ArtifactCard
    from harness_core.driver import AssembledRuntime
    from harness_core.effects.runner import ToolFn
    from harness_core.effects.types import (
        ApprovalDecision,
        ApprovalPolicy,
        ToolContext,
        ToolEffect,
    )
    from harness_core.engines.durable.ports import EngineAttempt, EngineWrapUp
    from harness_core.engines.events import EngineEvent, EngineState, NativeUnknown
    from harness_core.engines.native import TranscriptPart
    from harness_core.engines.types import EngineTask, FollowUp
    from harness_core.engines.types import JsonValue as EngineJson
    from harness_core.gateway.asking import AskedQuestion
    from harness_core.journal.types import (
        Claim,
        CompactAsk,
        InputArtifactRef,
        JournalEvent,
        Writer,
    )
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable.driver import Disposition
    from harness_core.knowledge.bulk_export import ShelfRelay
    from harness_core.provenance.stamp import ProvenanceStamp
    from harness_core.sandbox.types import LeaseRef, SandboxSpec
    from harness_core.security.labels import SourceLedger
    from harness_core.skills.types import SkillPin
    from harness_core.telemetry.ports import HarnessTracer
    from harness_core.testing.contracts.tool_catalog import OfferedTool
    from harness_core.testing.sandbox.egress import EgressRequest
    from harness_core.workspace.changes import CommitReason
    from harness_core.workspace.types import WorkspaceManifest

ORG, SESSION = "org-e1", "session-e1"
PROGRESS = "x.test.engine.progress"
# What a test host journals of an engine's native stream (C2.39, C2.40): never a progress line.
NATIVE_UNKNOWN = "x.test.native.unknown"
NATIVE_TRANSCRIPT = "x.test.native.transcript"
IMAGE = "fake-engine-image"
PLACEHOLDER_KEY = b"e1-durable-placeholder-key-0123456789"
LOOKUP = offered("lookup")
CREATE = offered("create", "write")
TOOLS = (LOOKUP, CREATE)


def engine_profile(**limits: int | float | None) -> EngineProfile:
    return EngineProfile(
        engine=FAKE_VERSION,
        mode=EngineMode.BUILD,
        access=AccessRouteRef("scripted:scripted-1", "d"),
        isolation_min=RuntimeMode.DOCKER_HARDENED,
        limits=RunLimits(**limits),  # type: ignore[arg-type]
    )


def engine_run_spec(
    profile: EngineProfile | None = None,
    *,
    digest: str | None = None,
    max_children: int = 0,
    mounts: Sequence[str] = (),
    tools: Sequence[OfferedTool] = TOOLS,
) -> RunSpec:
    """A v3 spec pinned with an engine executor and its profile (E4a's shape)."""
    profile = profile or engine_profile()
    engine: dict[str, JsonValue] = {
        "kind": profile.engine.kind,
        "version": profile.engine.version,
        "binaryDigest": profile.engine.binary_digest,
        "profileDigest": digest or profile_digest(profile),
        "profile": profile_json(profile),
    }
    return validate_contract(
        RunSpec,
        {
            "schemaVersion": 3,
            "executor": {"kind": ENGINE, "engine": engine},
            "harnessMajor": 2,
            "foldVersion": 8,
            "runKind": "interactive",
            "principalRef": "p_user",
            "model": {"policy": [{"provider": "scripted", "model": "scripted-1"}], "promptVersion": "p-test"},
            "grants": {
                "tools": [{"toolId": t.effect.tool_id, "descriptorDigest": "sha256:" + "1" * 64} for t in tools],
                "sandboxProfile": "none",
                "egress": "none",
            },
            "budgets": {
                "maxTurns": 8,
                "maxTokens": 1_000_000,
                "maxCostUsd": 1.0,
                "activeTimeS": 3600,
                "calendarDeadline": "2027-01-01T00:00:00Z",
                "maxChildren": max_children,
            },
            "capabilitiesDigest": "sha256:" + "0" * 64,
            "extensions": {"x.pipeshub": {"mounts": list(mounts)}} if mounts else {},
        },
    )


@dataclass
class RecordingPresenter:
    """Presents each engine event as one test extension event; records every call."""

    calls: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def _line(self, stamp: ProgressStamp, kind: str, text: str, data: Mapping[str, JsonValue] | None = None) -> NewEvent:
        self.calls.append((kind, text))
        return NewEvent(
            PROGRESS,
            {"taskId": stamp.task_id, "turnId": stamp.turn_id, "seq": stamp.allocate(), "kind": kind, "text": text,
             "data": dict(data or {})},
            critical=False,
        )

    def started(self, stamp: ProgressStamp, goal: str) -> list[NewEvent]:
        return [self._line(stamp, "started", goal)]

    def event(self, stamp: ProgressStamp, event: EngineEvent) -> list[NewEvent]:
        name = type(event).__name__
        if name in ("CommandStarted", "FileChanged", "ToolCallSeen", "CommandOutput"):
            return [self._line(stamp, name, "")]
        return []

    async def saved(self, stamp: ProgressStamp, manifest: WorkspaceManifest, reason: CommitReason) -> list[NewEvent]:
        return [self._line(stamp, "version", str(manifest.revision), {"reason": reason})]

    def artifact(self, stamp: ProgressStamp, card: ArtifactCard) -> list[NewEvent]:
        return [self._line(stamp, "artifact", card.name, {"lifecycle": card.lifecycle})]

    def approval(
        self, stamp: ProgressStamp, interaction_id: str, tool: str, arguments: Mapping[str, JsonValue]
    ) -> list[NewEvent]:
        return [self._line(stamp, "approval", tool, {"interactionId": interaction_id})]

    def tool_result(
        self, stamp: ProgressStamp, tool: str, arguments: Mapping[str, JsonValue], result: McpResult
    ) -> list[NewEvent]:
        return [self._line(stamp, "tool_result", tool, {"text": result.text, "isError": result.is_error})]

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
        data: dict[str, JsonValue] = {"success": success, "reason": reason}
        if budget_scope is not None:
            data["budgetScope"] = budget_scope
        return [self._line(stamp, "finished", output, data)]

    def question(self, stamp: ProgressStamp, asked: AskedQuestion) -> list[NewEvent]:
        return [self._line(stamp, "question", asked.question, {"callId": asked.call_id, "options": list(asked.options)})]

    def awaiting_answer(self, stamp: ProgressStamp, output: str) -> list[NewEvent]:
        return [self._line(stamp, "awaiting_answer", output)]

    def native_unknown(self, stamp: ProgressStamp, event: NativeUnknown, content: ContentRef) -> list[NewEvent]:
        data: dict[str, JsonValue] = {
            "turnId": stamp.turn_id, "kind": event.kind, "sizeBytes": event.size_bytes, "truncated": event.truncated,
            "content": content.model_dump(mode="json"),
        }
        return [NewEvent(NATIVE_UNKNOWN, data, critical=False)]

    def native_transcript(self, stamp: ProgressStamp, part: TranscriptPart, content: ContentRef) -> list[NewEvent]:
        data: dict[str, JsonValue] = {
            "turnId": part.turn_id, "part": part.index, "lines": part.lines, "sizeBytes": len(part.data),
            "final": part.final, "truncated": part.truncated, "content": content.model_dump(mode="json"),
        }
        return [NewEvent(NATIVE_TRANSCRIPT, data, critical=False)]


class WorkerKilled(BaseException):
    """The worker died at a step boundary: nothing of it catches this."""


class CountingBroker(FakeSandboxBroker):
    """The fake broker, counting acquisitions (a refusal must acquire nothing)."""

    acquisitions = 0

    @override
    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> FakeSandboxHandle:
        self.acquisitions += 1
        return await super().acquire(spec, lease=lease, idempotency_key=idempotency_key)


@dataclass
class EngineWorld:
    clock: ManualClock = field(default_factory=ManualClock)
    content: InMemoryContentStore = field(default_factory=InMemoryContentStore)
    objects: InMemoryBinaryObjectStore = field(default_factory=InMemoryBinaryObjectStore)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    publications: InMemoryPublicationLog = field(default_factory=InMemoryPublicationLog)
    presenter: RecordingPresenter = field(default_factory=RecordingPresenter)
    host: InMemoryToolGatewayHost = field(default_factory=InMemoryToolGatewayHost)
    wakeup: InMemoryInteractionWakeup = field(default_factory=InMemoryInteractionWakeup)
    skills: InMemorySkillContentSource = field(default_factory=InMemorySkillContentSource)
    # (tool, args digest) -> times the tool's effect reached the world.
    effects: Counter[tuple[str, str]] = field(default_factory=Counter[tuple[str, str]])
    approvals: Mapping[str, ApprovalDecision] | None = None
    lease_ms: int = 30_000
    adapter: FakeEngineAdapter = field(default_factory=lambda: ScriptedEngineAdapter())
    skill_pins: tuple[SkillPin, ...] = ()
    # A host's own event types, validated as Node validates them (its catalog).
    extension_writers: Mapping[str, frozenset[Writer]] = field(default_factory=lambda: dict[str, "frozenset[Writer]"]())
    extension_models: Mapping[str, type[BaseModel]] = field(default_factory=lambda: dict[str, "type[BaseModel]"]())
    # The engine profile a loop parent's org grants its engine children (as Node resolves it).
    engine_authority: JsonValue | None = None
    # The run's tools and, by name, what each does in the world (default: count the call and echo it).
    tools: tuple[OfferedTool, ...] = TOOLS
    handlers: Mapping[str, ToolFn] = field(default_factory=lambda: dict[str, "ToolFn"]())
    # A tool's own definition as the engine lists it (a product tool's description and parameters); default: a stub.
    schemas: Mapping[str, ToolSchema] = field(default_factory=lambda: dict[str, ToolSchema]())
    # The model the engine reaches through the egress proxy; default: the conformance gateway's scripted brain.
    model: Callable[[EgressRequest], Awaitable[EngineJson]] | None = None
    # Artifacts no run's actor may read (an input naming one is refused); ``None``: no input source at all.
    unreadable: set[str] | None = field(default_factory=set[str])
    # One input's cap (C3.08, J-26): the release's unless a test lowers it.
    input_max_bytes: int = MAX_INPUT_BYTES
    # Each attempt's source ledger (P2.5a); ``None``: none, so published files' sources stay unknown.
    ledger: Callable[[], SourceLedger] | None = None
    # The host's narrowing of the run's approvals over that ledger (SEC1's flow rule), as its assembler wires it.
    guard: Callable[[ApprovalPolicy, SourceLedger, str], ApprovalPolicy] | None = None
    # What sandbox snapshots hold (P5.9b); ``None``: none are taken or restored.
    snapshots: SnapshotPolicy | None = None
    # How long a snapshot may be restored after it was taken (OWD-12); ``None``: any age.
    snapshot_ttl: timedelta | None = None
    # Each attempt's own token values (a snapshot holding one is refused).
    secrets: tuple[str, ...] = ()
    # P9: where the kernel, controller and endpoint put their spans.
    tracer: HarnessTracer = field(default_factory=lambda: NOOP_TRACER)
    # UX2: a journal that plays a Node older than the worker (what claims accept, the event types it refuses).
    journal_accepts: frozenset[str] | None = None
    journal_refuses: frozenset[str] = frozenset()
    # The model routes the run's gateway token names (a real model gateway checks them); none for the fake proxy.
    routes: tuple[PinnedRoute, ...] = ()
    # Each attempt's wrap-up (ENGBUDGET), by its claim; ``None``: an unmetered run.
    wrap_up: Callable[[Claim], EngineWrapUp | None] | None = None
    # Each turn's usage read from the conformance gateway's ledger (OWD-8); false: the engine's own report.
    metered: bool = False
    # OWD-7: one gateway tool list per session and engine kind (``EngineToolPin``).
    tool_pin: bool = False
    # WP-84: what each attempt observes it was granted (the host's ``CapabilityCheck``), by its claim.
    capabilities: Callable[[Claim], Mapping[str, JsonValue]] | None = None
    # WP79-FU-5: what the kernel asked the host to withhold; observed with its recorded mark from then on.
    withheld: dict[str, JsonValue] = field(default_factory=lambda: {})
    # OWD-15: what the host journals as the run's conversation holds more, over each attempt's ledger.
    level_events: Callable[[SourceLedger, str], Callable[[], Sequence[NewEvent]]] | None = None
    # ADR-RUN-01 D3: each attempt's provenance stamp over the served tool names, as an assembler wires its rules;
    # ``None``: results are served unstamped.
    provenance: Callable[[Sequence[str]], ProvenanceStamp] | None = None
    # How often each turn's ticker runs, and how the controller sleeps: by default every tick and wait is 1 ms, so a
    # test does not wait on a worker's seconds. A rig that measures the worker's costs (PF-05) passes the worker's
    # (``TICK_S``, ``asyncio.sleep``): 1 ms ticks are a thousand wake-ups a second per run the worker never has.
    tick_s: float = 0.001
    controller_sleep: Callable[[float], Awaitable[None]] | None = None
    # G-52: the host's answer-language line for each run (``AssembledEngineRun.answer_language``); "": none.
    answer_language: str = ""
    # The app's facts about a task (``AssembledEngineRun.task_facts``), from its text; ``None``: none.
    task_facts: Callable[[str], str] | None = None
    # The app's hand-back shaper for a child run (``AssembledEngineRun.child_result``), from its task's text.
    child_result: Callable[[str], Callable[[dict[str, JsonValue], Sequence[JournalEvent]], dict[str, JsonValue]]] | None = None
    # The run's shelf relay, attached to each turn's endpoint as production attaches it (``durable.py``): a lane's
    # repository tool hands off and reports its fetches (ADR-RUN-01 D2's vouched references) through it.
    shelf_relay: ShelfRelay | None = None

    def __post_init__(self) -> None:
        self.store = InMemoryWorkspaceStore(clock=self.clock)
        self.writers = InMemoryWorkspaceWriters(clock=self.clock)
        self.journal = InMemoryJournal(
            clock=self.clock,
            lease_ms=self.lease_ms,
            extension_writers={
                PROGRESS: frozenset({"worker"}),
                NATIVE_UNKNOWN: frozenset({"worker"}),
                NATIVE_TRANSCRIPT: frozenset({"worker"}),
                OUTCOME_RECORDED: frozenset({"worker"}),
                OUTCOME_SETTLED: frozenset({"worker"}),
                **self.extension_writers,
            },
            extension_models=self.extension_models,
            workspaces=self.store,
            engine_authority=self.engine_authority,
            accepts=self.journal_accepts,
            refuses=self.journal_refuses,
        )
        self.gateway = ConformanceGateway(
            endpoints=EngineControllerConfig(images={}).endpoints("unused-0123456789", "unused-0123456789"),
            grant=[GrantedTool(t.name) for t in self.tools],
        )
        model = self.model or self.gateway._model  # pyright: ignore[reportPrivateUsage]
        # Tokens only for the attempt a binding names, as Node issues them (SIGNFIX).
        self.tokens = AttemptTokenSource(b"world-tokens", self.clock)
        self.proxy = FakeEgressProxy(clock=self.clock, gateways={"model-gateway": model}, tokens=self.tokens)
        self.proxy.route_upstream(self.host.base, self.host.handle)
        self.broker = CountingBroker(
            clock=self.clock, ids=SeededIds(), modes=frozenset({RuntimeMode.DOCKER_HARDENED}), egress_proxy=self.proxy
        )
        self.config = EngineControllerConfig(images={FAKE_VERSION: IMAGE}, tick_s=self.tick_s, snapshot=self.snapshots)

    def admit(
        self,
        run_id: str,
        prompt: str,
        *,
        spec: RunSpec | None = None,
        session: str = SESSION,
        principal: str = "p_user",
        inputs: Sequence[InputArtifactRef] = (),
        compact: CompactAsk | None = None,
        trigger: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """``inputs``: artifacts of the session the run's input names (E1b), found under ``in/``; ``compact``: the
        person's ``/compact`` taken into the run (SLASH2 C2); ``trigger``: what fired a scheduled run (TR-03)."""
        spec = spec or engine_run_spec(tools=self.tools)
        self.journal.admit(
            run_id, org_id=ORG, session_id=session, spec=spec, input_text=prompt, principal_ref=principal,
            input_artifacts=inputs, compact=compact, trigger_context=trigger,
        )

    def kernel(self, **overrides: object) -> EngineKernel:
        settings: dict[str, object] = {
            "journal": self.journal,
            "assembler": MemoryEngineAssembler(self),
            "clock": self.clock,
            "broker": self.broker,
            "presenter": self.presenter,
            "interactions": self.wakeup,
            "poll_s": 0.005,
            "flush_interval_s": 0.005,
            "sleep": _short,
            "tracer": self.tracer,
        }
        return EngineKernel(**{**settings, **overrides})  # type: ignore[arg-type]

    def driver(self, kernel: EngineKernel | None = None) -> RunDriver:
        return RunDriver(
            journal=self.journal,
            assembler=_NoLoop(),
            clock=self.clock,
            children=self.journal,
            interactions=self.wakeup,
            sleep=_short,
            kernels={ENGINE: kernel or self.kernel()},
        )

    async def engine_attempt(self, run_id: str) -> EngineAttempt:
        """An attempt of ``run_id`` as the kernel would hand it to an assembler (the run is claimed)."""
        claim = await self.journal.claim(run_id, worker_id="w-e1")
        assert claim is not None, f"{run_id} is not claimable"
        return self.kernel().attempt_of(claim, engine_profile())

    async def attempt(self, run_id: str, *, kernel: EngineKernel | None = None) -> Disposition:
        """One worker claims the run and drives one attempt."""
        claim = await self.journal.claim(run_id, worker_id="w-e1")
        assert claim is not None, f"{run_id} is not claimable"
        return await self.driver(kernel).run_claimed(claim, claim_sent_at=self.clock.suspend_aware())

    async def crash_at(
        self, run_id: str, point: str, *, nth: int = 1, attempt: asyncio.Future[Disposition] | None = None
    ) -> bool:
        """Run one attempt (or let ``attempt`` go on) and kill its worker at ``point`` (the ``nth`` hit):
        nothing of that worker runs after it, whichever task reached the point (the worker's own, or the
        engine's in-process tool call). ``False`` when the attempt never reached the point (it ended first)."""
        attempt = attempt if attempt is not None else asyncio.ensure_future(self.attempt(run_id))
        hit = False

        def kill() -> None:
            nonlocal hit
            hit = True
            _ = attempt.cancel()
            raise WorkerKilled(point)

        original = failpoints._kill9  # pyright: ignore[reportPrivateUsage]
        failpoints._kill9 = kill  # pyright: ignore[reportPrivateUsage]
        try:
            with armed({point: Arm(Kill9(), Nth(nth))}):
                with contextlib.suppress(asyncio.CancelledError, WorkerKilled):
                    _ = await attempt
        finally:
            failpoints._kill9 = original  # pyright: ignore[reportPrivateUsage]
        if hit:
            self.expire()
        return hit

    def expire(self) -> None:
        """A worker died: its lease lapses and the sweeper re-queues the run."""
        self.clock.advance(self.lease_ms / 1000 + 1)
        _ = self.journal.sweep()

    def events(self, run_id: str, session: str = SESSION) -> list[JournalEvent]:
        return [e for e in self.journal.events(session) if e.run_id == run_id]

    async def plain_answer(self, run_id: str | None = None, session: str = SESSION) -> str:
        """The run's final answer as a surface with no UI reads it (WP-89): the journaled text, and after it the
        presenter's closing when the settled outcome carries one (a chat that words the outcome got only the prose).
        ``run_id`` ``None``: the session's last final answer."""
        events = self.journal.events(session) if run_id is None else self.events(run_id, session)
        final = [e for e in events if e.type == "message.completed" and e.data.get("isFinal")][-1]
        ref = ContentRef.model_validate(final.data["textRef"])
        prose = (await self.content.get(ref)).decode("utf-8")
        settled = [e for e in events if e.type == OUTCOME_SETTLED and e.run_id == final.run_id]
        presented = settled[-1].data.get("presented") if settled else None
        closing = presented.get("closing") if isinstance(presented, dict) else None
        return Framed(prose, closing).text if isinstance(closing, str) else prose

    def types(self, run_id: str, session: str = SESSION) -> list[str]:
        return [e.type for e in self.events(run_id, session)]

    def duplicates(self) -> dict[tuple[str, str], int]:
        """External effects that reached the world more than once; a read has none, it may run again."""
        effectful = {t.name for t in self.tools if t.effect.needs_intent_commit}
        return {key: n for key, n in self.effects.items() if n > 1 and key[0] in effectful}

    def tool(self, name: str) -> ToolFn:
        handler = self.handlers.get(name)

        async def execute(call: ToolCall, ctx: ToolContext) -> ToolExecution:
            self.effects[(name, args_digest(call.arguments))] += 1
            if handler is not None:
                return await handler(call, ctx)
            return ToolExecution(status="ok", content=f"{name} done: {dict(call.arguments)}")

        return execute


@dataclass
class MemoryEngineAssembler:
    world: EngineWorld

    def controller(self, attempt: EngineAttempt) -> EngineController:
        """The attempt's controller: the world's fake engine and broker; a real lane overrides it."""
        w = self.world
        return EngineController(
            registry=AllowlistEngineRegistry([w.adapter], [FAKE_VERSION]),
            broker=w.broker,
            workspaces=w.store,
            sync_for=lambda scope: TarWorkspaceSync(objects=w.objects, scope=scope, clock=w.clock),
            strategies=SyncStrategies(),
            skill_bundles=StoredSkillBundleBuilder(source=w.skills, objects=w.objects, workspaces=w.store),
            skill_materializer=TarSkillMaterializer(objects=w.objects, workspaces=w.store),
            checkpoints=ObjectStoreEngineCheckpoints(w.objects),
            sink=attempt.sink,
            tool_host=w.host,
            clock=w.clock,
            placeholder_key=PLACEHOLDER_KEY,
            config=w.config,
            writers=w.writers,
            sleep=w.controller_sleep or _short,
            snapshots=ObjectStoreSandboxSnapshots(w.objects),
            tracer=w.tracer,
            shelf_relay=w.shelf_relay,
        )

    async def build(self, attempt: EngineAttempt) -> AssembledEngineRun:
        w = self.world
        claim = attempt.claim
        controller = self.controller(attempt)
        tools = [
            CatalogTool(
                w.schemas.get(t.name)
                or ToolSchema(name=t.name, description=f"{t.name} things", input_schema={"type": "object"}),
                t.effect,
                w.tool(t.name),
            )
            for t in w.tools
        ]
        catalog = InMemoryToolCatalog(tools, [t.effect.tool_id for t in w.tools])
        binding = RunAttemptBinding(
            tenant=claim.org_id,
            scope=claim.session_id,
            run_id=claim.run_id,
            parent_run_id=None,
            attempt_id=claim.attempt_id,
            fence=claim.fence,
            tool_ids_digest=grant_digest(t.effect.tool_id for t in w.tools),
            routes_digest=route_digest(w.routes) if w.routes else grant_digest(()),
        )
        approvals: ApprovalPolicy = (
            engine_default_approvals().narrowed(profile_rules(attempt.profile), "engine")
            if w.approvals is None
            else _Fixed(w.approvals)
        )
        ledger = w.ledger() if w.ledger is not None else None
        if w.guard is not None and ledger is not None:
            approvals = w.guard(approvals, ledger, claim.run_id)
        gated = gated_endpoint(claim.spec, catalog, approvals, attempt.run_state)
        endpoint = RunToolEndpoint(
            binding=binding,
            catalog=gated.catalog,
            effects=EffectRunner(
                attempt.effect_log,
                gated.approvals,
                hold=attempt.hold,
                guard=attempt.guard,
                propagate=attempt.propagate,
                dry_run=gated.dry_run,
            ),
            scope=InvocationScope(claim.run_id, claim.org_id, claim.attempt_id),
            ids=attempt.ids,
            citations=InMemoryCitationSink(),
            capabilities=controller.capabilities_for(attempt.profile.engine),
            hold_s=0.2,
            tracer=w.tracer,
            asks=asks_person(claim.spec),
            provenance=w.provenance([t.name for t in w.tools]) if w.provenance is not None else None,
        )
        return AssembledEngineRun(
            controller=controller,
            tools=endpoint,
            tool_binding=binding,
            grant_size=len(w.tools),
            artifacts=WorkspaceArtifactPublisher(
                w.artifacts, w.publications, w.objects, object_scope=lambda b: ObjectScope(b.tenant, f"ws:{b.scope}")
            ),
            content=w.content,
            idempotent={t.effect.tool_id: t.effect.idempotent for t in w.tools},
            workspaces=w.store,
            skills=w.skill_pins,
            inputs=(
                StoreInputSource(
                    w.artifacts, w.objects, object_scope=lambda b: session_scope(b.tenant, b.scope),
                    readable=lambda artifact_id: artifact_id not in (w.unreadable or set[str]()),
                    max_input_bytes=w.input_max_bytes,
                )
                if w.unreadable is not None else None
            ),
            ledger=ledger,
            secrets=lambda: w.secrets,
            objects=w.objects,
            wrap_up=w.wrap_up(claim) if w.wrap_up is not None else None,
            usage=w.gateway if w.metered else None,
            tool_pin=EngineToolPin(attempt.profile.engine.kind, HarnessLog.named("harness_core.testing.engines"))
            if w.tool_pin
            else None,
            level_events=w.level_events(ledger, claim.run_id)
            if w.level_events is not None and ledger is not None
            else None,
            snapshot_ttl=w.snapshot_ttl,
            capabilities=_observing(w.capabilities, claim, w.withheld) if w.capabilities is not None else None,
            withhold=_withholding(w.withheld) if w.capabilities is not None else None,
            answer_language=w.answer_language,
            task_facts=w.task_facts(claim.input_text) if w.task_facts is not None else "",
            child_result=w.child_result(claim.input_text) if w.child_result is not None else None,
        )


def _observing(
    capabilities: Callable[[Claim], Mapping[str, JsonValue]], claim: Claim, withheld: Mapping[str, JsonValue]
) -> Callable[[], Awaitable[Mapping[str, JsonValue]]]:
    async def observe() -> Mapping[str, JsonValue]:
        return {**capabilities(claim), **withheld}

    return observe


def _withholding(withheld: dict[str, JsonValue]) -> Callable[[Mapping[str, JsonValue]], Awaitable[None]]:
    async def withhold(marks: Mapping[str, JsonValue]) -> None:
        withheld.update(marks)

    return withhold


class ScriptedEngineAdapter(FakeEngineAdapter):
    """``FakeEngine`` whose next sessions can end their turn ``failed`` with an error code, or vanish without
    ending it (the process or sandbox went away); it records what each session was restored with."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.fail_with: list[str | None] = []
        self.restored: list[EngineState | None] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN001, ANN201
        self.restored.append(restore)
        session = await super().open(sandbox, launch, restore, correlation=correlation)
        if not self.fail_with:
            return session
        code = self.fail_with.pop(0)
        if code is not None and code.startswith(AFTER_TOOLS):
            return _FailsAtTheEndSession(session, code.removeprefix(AFTER_TOOLS))
        return _FailingSession(session, code)


# ``fail_with`` entry prefix: the session runs the turn's calls, then ends the turn ``failed`` instead of completing
# it (Codex 0.160 ends a turn failed when a tool it called came back deferred for an approval, FIX-P2-B3).
AFTER_TOOLS = "after_tools:"


class _FailsAtTheEndSession:
    def __init__(self, inner: object, code: str) -> None:
        self._inner = inner
        self._code = code

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        async for event in self._inner.run_turn(task):  # type: ignore[attr-defined]
            if isinstance(event, TurnCompleted):
                yield EngineError(EngineEventMeta(task.turn_id, event.meta.seq), self._code, "the engine stopped")
                yield TurnCompleted(EngineEventMeta(task.turn_id, event.meta.seq + 1), "failed")
                return
            yield event


class _FailingSession:
    """A session whose turn ends ``failed`` with ``code``, or (``None``) ends with no ``TurnCompleted``."""

    def __init__(self, inner: object, code: str | None) -> None:
        self._inner = inner
        self._code = code

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        turn_id = task.turn_id
        if self._code is not None:
            yield EngineError(EngineEventMeta(turn_id, 0), self._code, "the engine stopped")
            yield TurnCompleted(EngineEventMeta(turn_id, 1), "failed")


@dataclass(frozen=True)
class _Fixed:
    """Approvals by tool name: ``allow``, ``ask`` or ``deny``."""

    by_tool: Mapping[str, ApprovalDecision]

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        del effect
        return self.by_tool.get(call.name, "allow")


class _NoLoop:
    async def build(self, claim: Claim) -> AssembledRuntime:
        raise AssertionError("an engine world runs no loop runs")


async def _short(_s: float) -> None:
    await asyncio.sleep(0.001)


__all__ = [
    "AFTER_TOOLS",
    "NATIVE_TRANSCRIPT",
    "NATIVE_UNKNOWN",
    "CREATE",
    "CountingBroker",
    "LOOKUP",
    "ORG",
    "PROGRESS",
    "SESSION",
    "EngineWorld",
    "MemoryEngineAssembler",
    "RecordingPresenter",
    "ScriptedEngineAdapter",
    "WorkerKilled",
    "engine_profile",
    "engine_run_spec",
]

