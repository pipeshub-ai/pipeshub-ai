from __future__ import annotations

from app.agent_loop_lib.core.types import UserMessage
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.budget_guard import require_budget
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext, TurnContext

"""Deterministic per-turn guards, extracted from `Agent.run()`'s inline
`if config.budget...`/`if config.cancellation_token...`/"2 turns left"
checks into PRE_TURN/PRE_MODEL middleware — see `.claude/rules/principles.md`
rule 2/3 ("deterministic work -> hooks/middleware, never inline in the
probabilistic loop").

`install_turn_guards()` is the single entry point `Agent.__init__` calls to
wire these onto its `HookRegistry` kernel. It is idempotent per kernel
instance (not per `Agent` instance) because every `Agent` sharing one
`AgentRuntime` shares that runtime's single kernel by reference (see
`runtime/runtime.py::AgentRuntime.run_child`), so several `Agent` objects
can legitimately wrap the very same kernel — installing twice would
double-register `warn_before_deadline`, firing the deadline warning message
twice into context instead of once.

`install_turn_guards()` only ever installs the guards every agent needs
regardless of role (budget/cancellation/deadline-warning/truncation-
recovery — each already gated on its own config being present, or a cheap,
behavior-neutral nudge). `install_supervisor_confidence_gate()` and
`install_stall_detection()` below are deliberately NOT part of it: both
change model-visible behavior (blocking a tool result; injecting warning/
directive messages into context), so a role that never calls `create_plan`
or that legitimately needs many low-signal turns (e.g. a short-lived
`spawn_agent` researcher) shouldn't have them running unconditionally on
every turn. Add either to `AgentSpec.middleware` (or `ControlPlaneConfig.
hooks`, see `control_plane.py`) for the specific roles that want them.
"""

__all__ = [
    "check_not_cancelled",
    "install_stall_detection",
    "install_supervisor_confidence_gate",
    "install_turn_guards",
    "warn_before_deadline",
]

_INSTALLED_MARKER = "_agent_turn_guards_installed"
_SUPERVISOR_GATE_MARKER = "_agent_supervisor_confidence_gate_installed"
_STALL_DETECTION_MARKER = "_agent_stall_detection_installed"


def check_not_cancelled(cancellation_token: object):
    """PRE_TURN middleware: denies the turn once `cancellation_token.is_cancelled`.

    Marks `ctx.metadata["cancelled"] = True` alongside the deny so
    `agent.hook_dispatch.dispatch_pre_turn` can raise the distinct
    `RunCancelled` (rather than generic `HookBlocked`) — letting
    `Agent.run()` keep reporting cancellation via its own status/event shape
    (status="cancelled", `EventType.CANCELLATION`) exactly as before this
    check lived inline.
    """

    async def _middleware(ctx: TurnContext, next_fn) -> None:
        if cancellation_token is not None and cancellation_token.is_cancelled:
            ctx.metadata["cancelled"] = True
            ctx.deny("Cancelled")
            return
        await next_fn()

    return _middleware


def warn_before_deadline(warn_at_turns_left: int = 2):
    """PRE_MODEL middleware: nudges the model to wrap up `warn_at_turns_left`
    turns before `ctx.max_turns` is hit, so it can synthesize gracefully
    instead of running into the hard cap mid-thought."""

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        if ctx.max_turns is not None and (ctx.max_turns - ctx.turn_index) == warn_at_turns_left:
            ctx.messages.append(UserMessage(
                content=(
                    f"[System: You have {warn_at_turns_left} turns remaining. "
                    "Stop gathering information now. "
                    "Synthesise everything you have found and call task_complete immediately "
                    "with your best answer. Do not make any more search or scrape calls.]"
                ),
            ))
        await next_fn()

    return _middleware


def install_turn_guards(
    kernel,
    *,
    budget: object | None = None,
    cancellation_token: object | None = None,
) -> None:
    """Idempotently register the budget/cancellation/deadline-warning/
    truncation-recovery guards onto `kernel` — every deterministic per-turn
    concern that used to be an inline `if` check in the turn loop, and that
    every agent needs regardless of role. See this module's docstring for
    why `supervisor_confidence_gate`/`stall_detection` are opt-in instead of
    being installed here.
    """
    if getattr(kernel, _INSTALLED_MARKER, False):
        return
    setattr(kernel, _INSTALLED_MARKER, True)

    if budget is not None:
        kernel.on(HookEvent.PRE_TURN).use(require_budget(budget))
    if cancellation_token is not None:
        kernel.on(HookEvent.PRE_TURN).use(check_not_cancelled(cancellation_token))
    kernel.on(HookEvent.PRE_MODEL).use(warn_before_deadline())

    from app.agent_loop_lib.hooks.middleware.builtin.truncation_recovery import (
        default_truncation_recovery,
    )
    kernel.on(HookEvent.POST_MODEL).use(default_truncation_recovery())


def install_supervisor_confidence_gate(kernel) -> None:
    """Opt-in installer for `supervisor_confidence_gate()` (see
    `hooks/middleware/builtin/supervisor_gate.py`) — add to `AgentSpec.
    middleware` for roles that call `create_plan` and want LOW-confidence
    plans blocked automatically. Matches the `Callable[[HookRegistry],
    None]` shape `AgentSpec.middleware` expects, and is idempotent per
    kernel for the same reason `install_turn_guards` is (several `Agent`s
    can share one runtime's kernel)."""
    if getattr(kernel, _SUPERVISOR_GATE_MARKER, False):
        return
    setattr(kernel, _SUPERVISOR_GATE_MARKER, True)

    from app.agent_loop_lib.hooks.middleware.builtin.supervisor_gate import (
        supervisor_confidence_gate,
    )
    kernel.on(HookEvent.POST_TOOL_USE).use(supervisor_confidence_gate())


def install_stall_detection(kernel, **kwargs: object) -> None:
    """Opt-in installer for `stall_detection()` (see `hooks/middleware/
    builtin/stall_detection.py`) — add to `AgentSpec.middleware` for roles
    that run long enough to plausibly stall (e.g. a top-level ReAct/
    orchestrator agent), not for short-lived sub-agents where a couple of
    error-heavy turns is expected, not a stall. `kwargs` forwards to
    `stall_detection()` (`warn_after`/`fail_after`/`error_ratio`).

    Idempotent per kernel like the other installers here, but note this
    means the FIRST spec to install it on a shared kernel wins its
    thresholds for every OTHER agent sharing that kernel too — `AgentRuntime.
    run_child()` hands children the exact same runtime/kernel as their
    parent (see its docstring), so there is currently no way to give two
    specs on one runtime different thresholds; they'd need separate
    `AgentRuntime`s."""
    if getattr(kernel, _STALL_DETECTION_MARKER, False):
        return
    setattr(kernel, _STALL_DETECTION_MARKER, True)

    from app.agent_loop_lib.hooks.middleware.builtin.stall_detection import (
        stall_detection,
    )
    post_turn_mw, pre_model_mw = stall_detection(**kwargs)
    kernel.on(HookEvent.POST_TURN).use(post_turn_mw)
    kernel.on(HookEvent.PRE_MODEL).use(pre_model_mw)
