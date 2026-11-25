"""``EffectRunner``: classify, dedupe, record the intent, approve, execute, record the outcome.

Extracted from ``DurableKernel`` (F1). The kernel composes the steps itself
(``approve``, ``recover``, ``read``, ``perform``) because it plans a whole
batch before running any of it; ``run`` is the same steps for one call at a
time (the MCP endpoint, an engine re-calling a tool).

Approval keeps the kernel's semantics: the policy is asked before any intent
is recorded, so a call that waits for approval has recorded nothing. ``hold``
is the seam for deciding it while the call waits (``BridgeApprovalHold`` over an
``InteractionBridge``, P2.6c); without one, the call is ``held`` and the caller
defers it, as the kernel suspends without an interaction wakeup.
"""

from __future__ import annotations

import dataclasses
import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, cast, runtime_checkable

from harness_core.effects.ask_reason import card_reason
from harness_core.effects.edits import EDIT_UNREADABLE, edited_call, noted
from harness_core.effects.failures import retry_after_s, tool_error_code
from harness_core.effects.policy import OWN_SANDBOX_RULE, in_own_sandbox
from harness_core.effects.redaction import (
    POLICY_REDACTION,
    RedactsResults,
    either,
    redacted,
)
from harness_core.effects.types import (
    ALLOW_WITH_REDACTION,
    POLICY_DENIAL,
    EffectResult,
    ExplainsDenial,
    ToolExecution,
    allow_all,
    approval_denial,
    consult_policy,
    refresh_policy,
)
from harness_core.failpoints import afailpoint
from harness_core.gates.dry_run import DRY_RUN_RULE, shadowed, simulated
from harness_core.logs import HarnessLog
from harness_core.security.secrets import NO_SECRETS, active_secrets, masking
from harness_core.security.token_patterns import redact_tokens_in

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.effects.edits import ApprovedEdit
    from harness_core.effects.log import EffectLog
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import (
        ApprovalDecision,
        ApprovalPolicy,
        EffectCall,
        RecordedEffect,
        ToolContext,
        ToolEffect,
    )
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.security.secrets import CallSecrets

    ToolFn = Callable[[ToolCall, ToolContext], Awaitable[ToolExecution]]
    Settle = Callable[[ToolExecution], ToolExecution]

MAX_EFFECT_RETRIES = 3

HoldDecision = Literal["approved", "denied"]


class ApprovalHold(Protocol):
    async def hold(self, call: EffectCall) -> HoldDecision | None:
        """Wait for a person's decision on ``call``; ``None``: not decided in time,
        the caller defers the call and nothing is recorded."""
        ...


def _masked(execution: ToolExecution) -> ToolExecution:
    """The outcome as it may be journaled, shown to the model and presented: run inside the call's ``masking``.

    Every value the boundary resolved for the call is masked exactly wherever the tool put it (SEC4). A failure's
    text is also masked by vector: error text is a diagnosis, never the data a person asked for, and clients put
    what they hold in it, as an HTTP client's URL with its ``access_token`` (QA-14). Every failure carries a
    ``ToolErrorCode``: the person reads words chosen by it, never the text (ERR-UX).
    """
    if execution.status != "ok":
        execution = dataclasses.replace(
            execution,
            content=redact_tokens_in(execution.content),
            error_code=execution.error_code or ("timeout" if execution.status == "timeout" else "failed"),
        )
    exact = active_secrets()
    if not exact:
        return execution
    return dataclasses.replace(
        execution,
        content=exact.mask_in(execution.content),
        receipt=_masked_object(execution.receipt),
        events=tuple(dataclasses.replace(e, data=_masked_object(e.data)) for e in execution.events),
        slots={key: exact.mask_in(value) for key, value in execution.slots.items()},
        answer=None if execution.answer is None else exact.mask(execution.answer),
        meta=_masked_object(execution.meta),
    )


def _masked_object[M: Mapping[str, JsonValue] | None](value: M) -> M:
    return value if value is None else cast("M", active_secrets().mask_in(dict(value)))


@runtime_checkable
class ExplainingHold(Protocol):
    """A hold that journals why the policy asks with the request it makes (UX2: an engine run's approval card says
    what, where and why, as the loop's does)."""

    def explain(self, call: EffectCall, reason: Mapping[str, JsonValue]) -> None: ...


@runtime_checkable
class ReasonedHold(Protocol):
    """A hold that knows what the person said when they declined, so the refusal carries it (AP-05)."""

    def denial_reason(self, call: EffectCall) -> str | None: ...


@runtime_checkable
class EditingHold(Protocol):
    """A hold that knows the arguments a person approved a call with in place of its own (AP-06)."""

    def approved_edit(self, call: EffectCall) -> ApprovedEdit | None: ...


@runtime_checkable
class NamedHold(Protocol):
    """A hold that names the interaction a call waits on, so a deferred answer can cite it."""

    def interaction_for(self, call: EffectCall) -> str: ...


def _unguarded() -> None:
    return None


def _settled(execution: ToolExecution) -> ToolExecution:
    return execution


@dataclass
class EffectRunner:
    log: EffectLog
    approvals: ApprovalPolicy = allow_all
    hold: ApprovalHold | None = None
    max_retries: int = MAX_EFFECT_RETRIES
    # Checked before every execution; raises to stop (the kernel's lease check).
    guard: Callable[[], None] = _unguarded
    # Raised by an execution, these stop the run instead of becoming an outcome.
    propagate: tuple[type[BaseException], ...] = ()
    logs: HarnessLog = field(
        default_factory=lambda: HarnessLog.named("harness_core.effects")
    )
    # What the call's boundary resolved (an attempt's connections); masked exactly in what the call returns (SEC4).
    secrets: CallSecrets = NO_SECRETS
    # G-03: the run is a dry run (``gates.dryRun``): a shadowed call is recorded, never made (``harness_core.gates``).
    dry_run: bool = False
    # Calls (by call id) a dry run let through that would have asked a person: their outcome says so.
    _would_ask: set[str] = field(default_factory=set[str], init=False, repr=False)

    async def refresh(self) -> None:
        """Before a batch of ``approve`` calls: a policy over live state reads it here, not while it decides."""
        await refresh_policy(self.approvals)

    def shadowed(self, effect: ToolEffect) -> bool:
        """A dry run's call that is recorded as what the run would have done instead of being made."""
        return self.dry_run and shadowed(effect)

    async def consult(self, call: ToolCall, effect: ToolEffect) -> None:
        """Before ``approve`` of one call: a policy that asks something outside the run asks here (G-40)."""
        await consult_policy(self.approvals, call, effect)

    def approve(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        decision = self.approvals(call, effect)
        if decision == "deny" or not self.shadowed(effect):
            return decision
        # Nothing happens, so nobody is asked; a denial stays one, as it would be outside the dry run.
        if decision == "ask":
            self._would_ask.add(call.id)
        return "allow"

    def simulated(self, call: EffectCall) -> ToolExecution:
        self.logs.info(
            "tool.shadowed", invocationId=call.invocation_id, toolId=call.effect.tool_id, rule=DRY_RUN_RULE
        )
        return simulated(call, would_ask=call.call.id in self._would_ask)

    def allowed(self, call: EffectCall) -> EffectCall:
        """``call`` as the policy let it run without a person: code in the run's own sandbox names that rule in its
        intent and the log, so the timeline shows it as activity with the reason it did not ask (CODEAUTO)."""
        if not in_own_sandbox(call.effect):
            return call
        self.logs.info(
            "tool.allowed", invocationId=call.invocation_id, toolId=call.effect.tool_id, rule=OWN_SANDBOX_RULE
        )
        return dataclasses.replace(call, allowed_by=OWN_SANDBOX_RULE)

    def redaction(self, call: EffectCall) -> Redaction | None:
        """What the policy redacts from this call's result (``allow_with_redaction``), asked whenever the call runs:
        a person's approval or a retry after a crash never lifts a layer's redaction."""
        named = self.approvals.redaction(call.call, call.effect) if isinstance(self.approvals, RedactsResults) else None
        return either(call.redaction, named)

    def _redacted(self, call: EffectCall, execution: ToolExecution) -> ToolExecution:
        redaction = self.redaction(call)
        if redaction is None:
            return execution
        execution, kinds = redacted(execution, redaction)
        if kinds:
            self.logs.info(
                "tool.redacted",
                invocationId=call.invocation_id,
                toolId=call.effect.tool_id,
                reason=redaction.reason,
                kinds=sorted(kinds),
            )
        return execution

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        """(rule, text) of a ``deny`` from ``approve``."""
        return self.approvals.denial(call, effect) if isinstance(self.approvals, ExplainsDenial) else POLICY_DENIAL

    def recover(self, effect: ToolEffect) -> Literal["retry", "unknown"]:
        """An intent is recorded and its outcome is not: the effect may have happened, except in a dry run, where
        it never runs."""
        return "retry" if effect.idempotent or self.shadowed(effect) else "unknown"

    async def read(
        self, call: EffectCall, ctx: ToolContext, execute: ToolFn
    ) -> ToolExecution:
        """Run a call whose failure leaves nothing behind: an exception is an error outcome. A dry run's shadowed
        call comes here too (it leaves nothing behind), and is simulated."""
        with masking(self.secrets):
            self.guard()
            if self.shadowed(call.effect):
                return self.simulated(call)
            try:
                execution = await execute(call.call, ctx)
            except self.propagate:
                raise
            except Exception as exc:
                self.logs.warning(
                    "tool.error",
                    invocationId=call.invocation_id,
                    toolId=call.effect.tool_id,
                    errorType=type(exc).__name__,
                )
                execution = ToolExecution(
                    status="error",
                    content=f"{type(exc).__name__}: {exc}",
                    error_code=tool_error_code(exc),
                    retry_after_s=retry_after_s(exc),
                )
            self.logs.debug(
                "tool.outcome",
                invocationId=call.invocation_id,
                toolId=call.effect.tool_id,
                status=execution.status,
            )
            return self._redacted(call, _masked(execution))

    async def perform(
        self,
        call: EffectCall,
        ctx: ToolContext,
        execute: ToolFn,
        *,
        write_intent: bool,
        settle: Settle = _settled,
    ) -> ToolExecution | None:
        """Run a write/execute call; ``None`` when its effect is unknown (not recorded
        here: the caller records ``unknown`` where its commits put it).

        ``write_intent`` is false when the intent is already recorded (a retry).
        ``settle`` adjusts the execution before its outcome is recorded."""
        if write_intent:
            await self.log.intent(call)
            await afailpoint("worker.tool.after_intent")
        with masking(self.secrets):
            if self.shadowed(call.effect):
                # Whatever path led here, a dry run never makes the call.
                execution = self.simulated(call)
                await self.log.outcome(call, execution)
                return execution
            return await self._performed(call, ctx, execute, settle)

    async def _performed(
        self, call: EffectCall, ctx: ToolContext, execute: ToolFn, settle: Settle
    ) -> ToolExecution | None:
        tries = 0
        while True:
            self.guard()
            try:
                execution = await execute(call.call, ctx)
            except self.propagate:
                raise
            except Exception as exc:
                if call.effect.idempotent and tries < self.max_retries:
                    tries += 1
                    self.logs.warning(
                        "tool.retry",
                        invocationId=call.invocation_id,
                        toolId=call.effect.tool_id,
                        tries=tries,
                        errorType=type(exc).__name__,
                    )
                    continue
                self.logs.warning(
                    "tool.unknown",
                    invocationId=call.invocation_id,
                    toolId=call.effect.tool_id,
                    reason="effect_may_have_happened",
                    errorType=type(exc).__name__,
                )
                return None
            await afailpoint("worker.tool.after_effect")
            execution = self._redacted(call, _masked(settle(execution)))
            await self.log.outcome(call, execution)
            return execution

    async def run(
        self, call: EffectCall, ctx: ToolContext, execute: ToolFn
    ) -> EffectResult:
        if call.effect.needs_intent_commit:
            recorded = await self.log.find(call.key)
            if recorded is not None:
                replayed = await self._replay(recorded, call, ctx, execute)
                if replayed is not None:
                    return replayed
        await self.refresh()
        settle: Settle = _settled
        await self.consult(call.call, call.effect)
        decision = self.approve(call.call, call.effect)
        if decision == ALLOW_WITH_REDACTION and call.redaction is None:
            call = dataclasses.replace(call, redaction=self.redaction(call) or POLICY_REDACTION)
        if decision == "deny":
            return await self._refuse(call, *self.denial(call.call, call.effect))
        if decision == "ask":
            held = await self._hold(call)
            if held is None:
                named = self.hold if isinstance(self.hold, NamedHold) else None
                return EffectResult(
                    "held",
                    invocation_id=call.invocation_id,
                    interaction_id=named.interaction_for(call) if named else None,
                )
            if held == "denied":
                said = self.hold.denial_reason(call) if isinstance(self.hold, ReasonedHold) else None
                return await self._refuse(call, *approval_denial(said))
            edit = self.hold.approved_edit(call) if isinstance(self.hold, EditingHold) else None
            if edit is not None:
                if edit.arguments is None:
                    return await self._refuse(call, *EDIT_UNREADABLE)
                call = edited_call(call, edit.arguments)
                # The person's approval covers what they wrote, never what a rule refuses outright, nor what the
                # org's policy webhook refuses once it sees the edited arguments (G-40 beside AP-06).
                await self.consult(call.call, call.effect)
                if self.approve(call.call, call.effect) == "deny":
                    return await self._refuse(call, *self.denial(call.call, call.effect))
                settle = functools.partial(noted, arguments=edit.arguments)
                self.logs.info(
                    "tool.approval_edited", invocationId=call.invocation_id, toolId=call.effect.tool_id,
                    editDigest=edit.digest,
                )
        else:
            call = self.allowed(call)
        if not call.effect.needs_intent_commit or self.shadowed(call.effect):
            await self.log.intent(call)
            execution = settle(await self.read(call, ctx, execute))
            await self.log.outcome(call, execution)
            return EffectResult("executed", execution, call.invocation_id)
        return await self._effect(call, ctx, execute, write_intent=True, settle=settle)

    async def _replay(
        self,
        recorded: RecordedEffect,
        call: EffectCall,
        ctx: ToolContext,
        execute: ToolFn,
    ) -> EffectResult | None:
        """The answer for a call whose key is already recorded; ``None``: run it."""
        fields = {
            "invocationId": call.invocation_id,
            "toolId": call.effect.tool_id,
            "recordedInvocationId": recorded.invocation_id,
            "state": recorded.state,
        }
        match recorded.state:
            case "completed":
                execution = recorded.execution
                if execution is None or execution.status != "ok":
                    return None
                self.logs.info("tool.dedupe", choice="recorded", **fields)
                return EffectResult("recorded", execution, recorded.invocation_id)
            case "unknown":
                self.logs.info("tool.dedupe", choice="unknown", **fields)
                return EffectResult("unknown", invocation_id=recorded.invocation_id)
            case "intent":
                if recorded.invocation_id != call.invocation_id:
                    self.logs.info("tool.dedupe", choice="in_flight", **fields)
                    return EffectResult(
                        "in_flight", invocation_id=recorded.invocation_id
                    )
                choice = self.recover(call.effect)
                self.logs.info("tool.recovery", choice=choice, **fields)
                if choice == "retry":
                    return await self._effect(call, ctx, execute, write_intent=False)
                await self.log.unknown(call)
                return EffectResult("unknown", invocation_id=call.invocation_id)

    async def _effect(
        self, call: EffectCall, ctx: ToolContext, execute: ToolFn, *, write_intent: bool, settle: Settle = _settled
    ) -> EffectResult:
        execution = await self.perform(call, ctx, execute, write_intent=write_intent, settle=settle)
        if execution is None:
            await self.log.unknown(call)
            return EffectResult("unknown", invocation_id=call.invocation_id)
        return EffectResult("executed", execution, call.invocation_id)

    async def _hold(self, call: EffectCall) -> HoldDecision | None:
        if self.hold is None:
            decision = None
        else:
            if isinstance(self.hold, ExplainingHold):
                # Never without a reason (WP-97): a policy that does not name its rule gets the plain one.
                self.hold.explain(call, card_reason(self.approvals, call.call, call.effect, self.logs))
            decision = await self.hold.hold(call)
        self.logs.info(
            "tool.approval",
            invocationId=call.invocation_id,
            toolId=call.effect.tool_id,
            decision=decision or "deferred",
        )
        return decision

    async def _refuse(self, call: EffectCall, rule: str, reason: str) -> EffectResult:
        await self.log.refused(call, rule, reason)
        self.logs.info(
            "tool.denied",
            invocationId=call.invocation_id,
            toolId=call.effect.tool_id,
            rule=rule,
        )
        return EffectResult(
            "denied", ToolExecution(status="error", content=reason), call.invocation_id
        )


__all__ = [
    "MAX_EFFECT_RETRIES",
    "ApprovalHold",
    "EditingHold",
    "EffectRunner",
    "ExplainingHold",
    "HoldDecision",
    "NamedHold",
    "ReasonedHold",
]
