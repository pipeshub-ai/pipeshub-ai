"""``RunDriver.run_claimed`` (REPORT 6.2.1): one claimed attempt, end to end.

The driver asks the ``RuntimeAssembler`` for the runtime the pinned spec
grants, runs the durable kernel on it inside that runtime's own bindings, and
settles the attempt itself when no runtime can be built:

* ``CapabilityLost`` (a pinned capability is gone or changed) yields the run
  for a bounded re-check, then fails it ``capability_lost``; it never runs on
  less than it was pinned with (PA-7, ``kernel.durable.capability``).
* ``RunRefused`` (the run can never execute, e.g. its agent was deleted)
  fails it terminally.
* Anything else is treated like a crash: the attempt is released, and the
  claim that finds ``attempt`` past the cap quarantines it.

Its own journal calls ride out a transient outage like the kernel's (PA-21):
retried with the same command while the lease holds, then the claim is
released so a later attempt settles the run.

The same code serves the dispatched worker and the inline (query service)
profile.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from harness_core import bindings
from harness_core.driver.kernels import LOOP, ExecutorUnavailable, KernelRegistry
from harness_core.driver.refusal import RunRefused
from harness_core.effects.reconcile import TimedReconciler
from harness_core.journal.types import (
    CommitRequest,
    Fail,
    JournalError,
    OwnershipLost,
    Quarantine,
    Yield,
    db_timestamp,
)
from harness_core.kernel.durable.capability import (
    CAPABILITY_LOST,
    capability_waits,
    recheck_delay,
)
from harness_core.kernel.durable.driver import (
    MAX_ATTEMPTS,
    Disposition,
    DispositionStatus,
    DurableKernel,
)
from harness_core.kernel.durable.lease import JournalGone, Lease, while_leased
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from harness_core.sandbox.errors import IsolationUnavailable, SandboxRefusal
from harness_core.security.secrets import NO_SECRETS
from harness_core.telemetry.memory import NOOP_TRACER

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.bindings import KernelBindings
    from harness_core.clock import Clock, StoreTime
    from harness_core.driver.kernels import ClaimedKernel
    from harness_core.effects.reconcile import ReconcileLookup
    from harness_core.gates.verify import VerifyPorts
    from harness_core.interactions.connect import ConnectGate
    from harness_core.interactions.ports import InteractionWakeup
    from harness_core.journal.ports import (
        CheckpointPort,
        ChildRunPort,
        ContentStorePort,
        JournalPort,
        LiveSinkPort,
    )
    from harness_core.journal.types import Claim, NewEvent, Transition
    from harness_core.kernel.durable.audience import HistoryAudience
    from harness_core.kernel.durable.context_compiler import ContextCompiler
    from harness_core.kernel.durable.delegation import DelegationTools
    from harness_core.kernel.durable.ports import (
        AfterRunStep,
        ApprovalPolicy,
        CapabilityCheck,
        ChildResultShaper,
        DurableModelPort,
        DurableToolPort,
        ImportedHistory,
        InputRenderer,
        LoopDirector,
        PostRunStep,
        PreRunGate,
        PreRunStep,
        ResponseReview,
        RunPresenter,
        RunRestore,
        TurnHook,
        TurnShaper,
        WrapUpPort,
    )
    from harness_core.models.policy import ModelPolicyConfig
    from harness_core.priority import PreemptRequests
    from harness_core.security.labels import SourceLedger
    from harness_core.security.secrets import CallSecrets
    from harness_core.telemetry.ports import HarnessTracer
    from harness_core.workspace.ports import ChildWorkspaces


class CapabilityLost(Exception):
    """A capability the run was pinned with is missing or no longer matches its digest."""

    def __init__(
        self, *, missing: Sequence[str] = (), changed: Sequence[str] = ()
    ) -> None:
        self.missing = tuple(sorted(missing))
        self.changed = tuple(sorted(changed))
        super().__init__(
            f"capability lost: missing={list(self.missing)} changed={list(self.changed)}"
        )


ISOLATION_UNAVAILABLE = IsolationUnavailable.code


def _refusal_type(refused: RunRefused | SandboxRefusal) -> str:
    return refused.error_type if isinstance(refused, RunRefused) else refused.code


@dataclass
class AssembledRuntime:
    """What one claimed attempt runs with. Holds no credentials the kernel can see:
    tool and model ports resolve their own inside the trusted process; ``secrets``
    only masks what they resolved in each call's outcome (SEC4)."""

    model: DurableModelPort
    tools: DurableToolPort
    content: ContentStorePort
    bindings: KernelBindings
    system: str | None = None
    approval: ApprovalPolicy = allow_all
    # What the run's session has read (P2.5a), fed by the kernel; set when ``approval`` reads it (SEC1).
    ledger: SourceLedger | None = None
    # The items this attempt's tools need that the run's owner has not connected (G-12): their calls wait.
    connect: ConnectGate | None = None
    # Events recording what the run's conversation now holds (OWD-15: its classification level), asked after each
    # commit the ledger has seen; they ride with the run's next commit. ``None``: the host records nothing.
    level_events: Callable[[], Sequence[NewEvent]] | None = None
    pre_run: Sequence[PreRunStep] = ()
    post_run: Sequence[PostRunStep] = ()
    # Work after the answer that never delays the run's end (UX5's suggested next questions).
    after_run: Sequence[AfterRunStep] = ()
    pre_run_gate: PreRunGate | None = None
    turn_hook: TurnHook | None = None
    capability_check: CapabilityCheck | None = None
    # Builds the tools a turn's calls name before they are planned (D-LAZY-4: an MCP server listed at first use).
    prepare_calls: Callable[[Sequence[str]], Awaitable[Mapping[str, str] | None]] | None = None
    # The run's MCP first-use pins, carried into each child it spawns (D-LAZY-5, R31HOT2); unset: none.
    mcp_pins: Callable[[], Mapping[str, Mapping[str, str]]] | None = None
    presenter: RunPresenter | None = None
    shaper: TurnShaper | None = None
    review: ResponseReview | None = None
    # What the run's verify gate (G-05) may look at: its workspace, its sources, its sandbox.
    verify: VerifyPorts | None = None
    restore: RunRestore | None = None
    inputs: InputRenderer | None = None
    history: ImportedHistory | None = None
    # A shared session's run (SS2): whose turns its model sees, as the host pinned it.
    audience: HistoryAudience | None = None
    # Reconcile hooks of the granted tools by tool id (P2.9); unset: unknown effects go to a person.
    reconcile_hooks: Mapping[str, ReconcileLookup] | None = None
    # Child runs and loop phases (P6.1, K-L): which calls delegate, the
    # strategy's director, and how a child completes its result.
    delegation: DelegationTools | None = None
    director: LoopDirector | None = None
    child_result: ChildResultShaper | None = None
    # The context compiler (P4.1/P4.2); unset, requests are assembled by ``shaper`` as before.
    context: ContextCompiler | None = None
    # What summarises for the person's ``/compact`` when ``context`` is unset (SLASH2 C2).
    compactor: ContextCompiler | None = None
    auto_compact: bool = False
    # Retries and fallback over the pinned routes (P4.7); set, ``model`` is a ``RoutedModelPort``.
    model_policy: ModelPolicyConfig | None = None
    # The run's wrap-up reserve (BUDGETEND): what its answer-only turn after the spending limit may spend.
    wrap_up: WrapUpPort | None = None
    secrets: CallSecrets = NO_SECRETS
    # Wraps the worker's live sink for this attempt (a product's additions to its frames); unset, frames go as sampled.
    live: Callable[[LiveSinkPort], LiveSinkPort] | None = None
    close: Callable[[], Awaitable[None]] | None = None


async def _close(close: Callable[[], Awaitable[None]], log: HarnessLog) -> None:
    """Releases the attempt's processes and sandboxes. Shielded so that a second
    cancellation (worker shutdown) cannot abandon them half-closed; a failure
    is logged and never replaces the disposition the attempt already settled."""
    try:
        await asyncio.shield(close())
    except Exception as exc:
        log.error("run.close_failed", exc=exc, errorType=type(exc).__name__)


class RuntimeAssembler(Protocol):
    """Builds exactly what the pinned ``RunSpec`` grants (REPORT 6.2.1 composition root).

    Raises ``CapabilityLost`` or ``RunRefused``; any other error counts as a crash.
    """

    async def build(self, claim: Claim) -> AssembledRuntime: ...


@dataclass
class RunDriver:
    journal: JournalPort
    assembler: RuntimeAssembler
    clock: Clock
    checkpoints: CheckpointPort | None = None
    live: LiveSinkPort | None = None
    # The admission authority a delegating run asks for its children (Node).
    children: ChildRunPort | None = None
    # Where a joined child's workspace fork is merged into its parent's (P6.4).
    child_workspaces: ChildWorkspaces | None = None
    renew_interval_s: float | None = None
    # Set: approvals are held in the running run for up to the hold (K-H, P2.6c).
    interactions: InteractionWakeup | None = None
    # Set: a Stop wakes the model call or reads in flight (UX3); unset, a Stop waits for the step.
    cancel_wakeup: InteractionWakeup | None = None
    lease_margin_s: float = 2.0
    max_attempts: int = MAX_ATTEMPTS
    journal_retry_s: float = 0.2
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # G-45: the attempts the claimer asked to give their slot to a higher class; the loop's kernels read it.
    preempt: PreemptRequests | None = None
    # OB-01: a loop run's spans (an engine kernel brings its own tracer).
    tracer: HarnessTracer = NOOP_TRACER
    # Executor kinds beyond the loop, each with its kernel (E1 registers ``engine``).
    kernels: Mapping[str, ClaimedKernel] = field(
        default_factory=dict[str, "ClaimedKernel"]
    )
    _running: dict[int, DurableKernel] = field(
        default_factory=dict[int, DurableKernel], init=False
    )
    _draining: bool = field(default=False, init=False)
    _registry: KernelRegistry[ClaimedKernel] = field(init=False)

    def __post_init__(self) -> None:
        self._registry = KernelRegistry(
            [(LOOP, _LoopKernel(self._run_loop, self._running)), *self.kernels.items()]
        )

    def request_drain(self) -> None:
        """SIGTERM: every running attempt yields at its next boundary."""
        self._draining = True
        for kernel in self._registry.kernels():
            kernel.request_drain()

    async def run_claimed(
        self, claim: Claim, *, claim_sent_at: float | None = None
    ) -> Disposition:
        sent_at = self.clock.suspend_aware() if claim_sent_at is None else claim_sent_at
        attempt = self._attempt(claim, sent_at)
        attempt.log.info(
            "run.claimed",
            attempt=claim.attempt,
            lastSeq=claim.last_seq,
            leaseMs=claim.lease_ms,
        )
        if self._draining:
            # A claim answered after SIGTERM: no assembly, the next worker takes it now.
            return await self._release(attempt, "drain")
        try:
            kernel = self._registry.kernel_for(claim.spec)
        except ExecutorUnavailable as refused:
            return await self._refuse(attempt, refused, executorKind=refused.kind)
        return await kernel.run_claimed(claim, claim_sent_at=sent_at)

    def _attempt(self, claim: Claim, sent_at: float) -> _Attempt:
        lease = Lease(
            self.clock, sent_at=sent_at, lease_ms=claim.lease_ms, margin_s=self.lease_margin_s
        )
        return _Attempt(
            claim, HarnessLog.for_claim("harness_core.driver", claim), lease, claim.store_time(self.clock, sent_at=sent_at)
        )

    async def _run_loop(self, claim: Claim, sent_at: float) -> Disposition:
        """The executor ``loop``: assemble the runtime the spec grants, run ``DurableKernel`` on it."""
        attempt = self._attempt(claim, sent_at)
        log = attempt.log
        started = self.clock.suspend_aware()
        try:
            runtime = await self.assembler.build(claim)
        except CapabilityLost as lost:
            return await self._capability_lost(attempt, lost)
        # A sandbox the run needs that this host or broker refuses (isolation, policy, image) is as final.
        except (RunRefused, SandboxRefusal) as refused:
            detail = {"reasonCode": refused.reason} if isinstance(refused, SandboxRefusal) else {}
            return await self._refuse(attempt, refused, **detail)
        except Exception as exc:
            log.error("run.assembly_failed", exc=exc, errorType=type(exc).__name__)
            return await self._build_failed(attempt)
        schemas = runtime.tools.schemas()
        # A shaper builds the system prompt each turn, after the pre-run steps decide the goal it is written for: no
        # prompt exists yet to count, and 0 read as "an empty prompt" (R43MCP-FU-5). The host logs the first one sent.
        system: dict[str, object] = (
            {"systemChars": len(runtime.system or "")} if runtime.shaper is None or runtime.system else {"system": "per_turn"}
        )
        log.info(
            "run.assembled",
            tools=len(schemas or []),
            **system,
            capabilityCheck=runtime.capability_check is not None,
            ms=round((self.clock.suspend_aware() - started) * 1000),
        )
        kernel = DurableKernel(
            journal=self.journal,
            content=runtime.content,
            model=runtime.model,
            tools=runtime.tools,
            clock=self.clock,
            checkpoints=self.checkpoints,
            live=self.live if self.live is None or runtime.live is None else runtime.live(self.live),
            approval=runtime.approval,
            ledger=runtime.ledger,
            level_events=runtime.level_events,
            interactions=self.interactions,
            connect=runtime.connect,
            cancel_wakeup=self.cancel_wakeup,
            pre_run=runtime.pre_run,
            post_run=runtime.post_run,
            after_run=runtime.after_run,
            pre_run_gate=runtime.pre_run_gate,
            turn_hook=runtime.turn_hook,
            capability_check=runtime.capability_check,
            prepare_calls=runtime.prepare_calls,
            mcp_pins=runtime.mcp_pins,
            presenter=runtime.presenter,
            shaper=runtime.shaper,
            review=runtime.review,
            verify=runtime.verify,
            restore=runtime.restore,
            inputs=runtime.inputs,
            history=runtime.history,
            audience=runtime.audience,
            children=self.children,
            delegation=runtime.delegation,
            director=runtime.director,
            child_result=runtime.child_result,
            child_workspaces=self.child_workspaces,
            context=runtime.context,
            compactor=runtime.compactor,
            auto_compact=runtime.auto_compact,
            model_policy=runtime.model_policy,
            wrap_up=runtime.wrap_up,
            secrets=runtime.secrets,
            reconciler=(
                TimedReconciler(runtime.reconcile_hooks, self.clock, self.sleep, log=log)
                if runtime.reconcile_hooks
                else None
            ),
            system=runtime.system,
            lease_margin_s=self.lease_margin_s,
            renew_interval_s=self.renew_interval_s,
            max_attempts=self.max_attempts,
            journal_retry_s=self.journal_retry_s,
            sleep=self.sleep,
            preempt=self.preempt,
            tracer=self.tracer,
        )
        if self._draining:
            kernel.request_drain()
        self._running[id(kernel)] = kernel
        try:
            with bindings.scoped(runtime.bindings):
                disposition = await kernel.run_claimed(claim, claim_sent_at=sent_at)
            log.info(
                "run.disposition",
                status=disposition.status,
                reason=disposition.reason,
                ms=round((self.clock.suspend_aware() - started) * 1000),
            )
            return disposition
        finally:
            _ = self._running.pop(id(kernel), None)
            if runtime.close is not None:
                await _close(runtime.close, log)

    async def _refuse(
        self, attempt: _Attempt, refused: RunRefused | SandboxRefusal, **detail: str
    ) -> Disposition:
        error_type = _refusal_type(refused)
        attempt.log.warning("run.refused", errorType=error_type, **detail)
        return await self._settle(attempt, "refused", Fail(error_type, str(refused)[:500]), "failed")

    async def _capability_lost(
        self, attempt: _Attempt, lost: CapabilityLost
    ) -> Disposition:
        claim, log = attempt.claim, attempt.log
        fields = {
            "reason": "pinned_capability_unavailable",
            "missing": list(lost.missing),
            "changed": list(lost.changed),
        }
        try:
            waited = await self._retrying(
                attempt,
                "capability_read",
                lambda: capability_waits(self.journal, claim),
            )
        except JournalGone:
            return await self._release(attempt, "journal_unavailable")
        delay = recheck_delay(waited)
        if delay is not None:
            log.info("run.capability_wait", recheck=waited + 1, delayS=delay, **fields)
            not_before = db_timestamp(attempt.store_time.now() + timedelta(seconds=delay))
            return await self._settle(
                attempt,
                "capability_wait",
                Yield(not_before, "capability_wait"),
                "yielded",
                "capability_wait",
            )
        log.warning("run.capability_lost", rechecks=waited, outcome="failed", **fields)
        return await self._settle(
            attempt, "capability_lost", Fail(CAPABILITY_LOST), "failed"
        )

    async def _build_failed(self, attempt: _Attempt) -> Disposition:
        claim = attempt.claim
        if claim.attempt >= self.max_attempts:
            attempt.log.warning(
                "run.quarantine",
                reason="assembly_failed",
                attempt=claim.attempt,
                maxAttempts=self.max_attempts,
            )
            return await self._settle(
                attempt,
                "assembly_failed",
                Quarantine(attempts=claim.attempt, last_error_type="assembly_failed"),
                "quarantined",
                "assembly_failed",
            )
        return await self._release(attempt, "assembly_failed")

    async def _release(self, attempt: _Attempt, reason: str) -> Disposition:
        try:
            await self.journal.release(attempt.claim.token, reason=reason)
        except JournalError as released:
            attempt.log.warning(
                "run.release_failed", reason=reason, errorType=type(released).__name__
            )
            return Disposition("lost", f"{reason}; release failed")
        attempt.log.info("run.release", reason=reason)
        return Disposition("released", reason)

    async def _settle(
        self,
        attempt: _Attempt,
        kind: str,
        transition: Transition,
        status: DispositionStatus,
        reason: str | None = None,
    ) -> Disposition:
        claim, log = attempt.claim, attempt.log
        # Built once: every retry is the same command, so a lost ack replays
        # instead of settling twice.
        request = CommitRequest(
            f"{claim.attempt_id}:driver:{status}", claim.token, (), transition
        )
        try:
            _ = await self._retrying(
                attempt,
                kind,
                lambda: self.journal.commit(request),
                commandId=request.command_id,
            )
        except JournalGone:
            return await self._release(attempt, "journal_unavailable")
        except OwnershipLost as lost:
            log.warning(
                "run.lease_lost",
                reason=type(lost).__name__,
                cause=str(lost),
                commandId=request.command_id,
            )
            return Disposition("lost", str(lost))
        settled = reason or getattr(transition, "error_type", None)
        log.info(
            "run.settled", status=status, reason=settled, commandId=request.command_id
        )
        return Disposition(status, settled)

    async def _retrying[T](
        self,
        attempt: _Attempt,
        kind: str,
        call: Callable[[], Awaitable[T]],
        **fields: str,
    ) -> T:
        return await while_leased(
            call,
            lease=attempt.lease,
            retry_s=self.journal_retry_s,
            sleep=self.sleep,
            on_retry=lambda retries: attempt.log.warning(
                "run.settle_retry",
                kind=kind,
                retries=retries,
                retryS=self.journal_retry_s,
                **fields,
            ),
        )


@dataclass(frozen=True)
class _LoopKernel:
    """The executor ``loop`` as a registered kernel: the driver's own durable path."""

    run: Callable[[Claim, float], Awaitable[Disposition]]
    running: Mapping[int, DurableKernel]

    async def run_claimed(self, claim: Claim, *, claim_sent_at: float) -> Disposition:
        return await self.run(claim, claim_sent_at)

    def request_drain(self) -> None:
        for kernel in self.running.values():
            kernel.request_drain()


@dataclass(frozen=True)
class _Attempt:
    claim: Claim
    log: HarnessLog
    lease: Lease
    store_time: StoreTime


__all__ = [
    "AssembledRuntime",
    "CapabilityLost",
    "RunDriver",
    "RunRefused",
    "RuntimeAssembler",
]
