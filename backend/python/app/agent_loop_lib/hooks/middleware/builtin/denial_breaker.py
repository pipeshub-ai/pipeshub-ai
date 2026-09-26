"""Deny-and-continue circuit breaker.

A denied tool call is returned to the model as an error and the run goes on;
that is right for an occasional denial, but a model that keeps calling blocked
tools spends every remaining turn on them. This counts PRE_TOOL_USE denials per
run and, after `MAX_CONSECUTIVE_DENIALS` in a row or `MAX_TOTAL_DENIALS` in all,
denies the next turn so the run ends with an error starting with
`DENIAL_STOP_MARKER`.

The PRE_TOOL_USE middleware must be registered before every middleware that can
deny: it observes the decision after `next_fn()` returns.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext, TurnContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision

__all__ = [
    "DENIAL_STOP_MARKER",
    "MAX_CONSECUTIVE_DENIALS",
    "MAX_TOTAL_DENIALS",
    "denial_breaker",
]

DENIAL_STOP_MARKER = "[agent_loop_stopped:tool_denials]"
MAX_CONSECUTIVE_DENIALS = 3
MAX_TOTAL_DENIALS = 20


@dataclass
class _DenialState:
    consecutive: int = 0
    total: int = 0
    stop_reason: str | None = None


_DENIAL_SLOT: StateSlot[_DenialState] = StateSlot(
    key="denial_breaker.state",
    default_factory=_DenialState,
)


def _state(scope) -> _DenialState | None:
    """`scope` is a `ToolScope` (PRE_TOOL_USE) or a `TurnScope` (PRE_TURN)."""
    if scope is None:
        return None
    turn = getattr(scope, "turn", scope)
    return turn.run.get(_DENIAL_SLOT)


def denial_breaker(
    *,
    max_consecutive: int = MAX_CONSECUTIVE_DENIALS,
    max_total: int = MAX_TOTAL_DENIALS,
):
    """Returns `(pre_tool_use_mw, pre_turn_mw)`."""

    async def _pre_tool_use(ctx: ToolCallContext, next_fn) -> None:
        await next_fn()
        state = _state(ctx.scope)
        if state is None:
            return
        if ctx.decision != PreDecision.DENY:
            state.consecutive = 0
            return
        state.consecutive += 1
        state.total += 1
        if state.stop_reason is None and (state.consecutive >= max_consecutive or state.total >= max_total):
            state.stop_reason = (
                f"{DENIAL_STOP_MARKER} {state.consecutive} consecutive / {state.total} total tool "
                f"calls were denied; last: {ctx.decision_reason}"
            )

    async def _pre_turn(ctx: TurnContext, next_fn) -> None:
        state = _state(ctx.scope)
        if state is not None and state.stop_reason:
            ctx.deny(state.stop_reason)
            return
        await next_fn()

    return _pre_tool_use, _pre_turn
