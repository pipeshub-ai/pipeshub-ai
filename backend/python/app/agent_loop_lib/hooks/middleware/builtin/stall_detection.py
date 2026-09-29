"""Stall detection: a POST_TURN + PRE_MODEL middleware pair that detects
when an agent is making no forward progress (repeated error-heavy turns)
and intervenes — first with a warning note on the latest tool result's
loop footer, then by escalating the warning to a hard "stop retrying" directive.

Principle 2/3: "deterministic work -> hooks/middleware, never LLM
judgment." The agent CANNOT be trusted to notice its own stall — that
recognition is a programmatic concern, not a probabilistic one.

State is carried across turns via a `StateSlot` on `RunScope`, the
idiomatic mechanism for cross-turn middleware state. Each run gets its
own slot value (no leakage between runs sharing the same kernel).

Thresholds (configurable via `stall_detection()`):
    warn_after:  consecutive error-heavy turns before injecting a warning
    fail_after:  consecutive error-heavy turns before the hard directive
    error_ratio: fraction of tool results that must be errors for a turn
                 to count as "error-heavy" (default 0.5 = majority failing)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.agent_loop_lib.core.finish_moves import finish_move
from app.agent_loop_lib.core.messages import ToolMessage, UserMessage
from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext, TurnContext

__all__ = ["stall_detection"]

logger = logging.getLogger(__name__)


@dataclass(frozen=False)
class _StallState:
    consecutive_error_turns: int = 0
    warned: bool = False
    total_error_calls: int = 0
    recent_error_tools: list[str] = field(default_factory=list)


_STALL_SLOT: StateSlot[_StallState] = StateSlot(
    key="stall_detection.state",
    default_factory=_StallState,
)


def _get_state(scope) -> _StallState | None:
    """Extract stall state from scope, returning None if no scope."""
    if scope is None:
        return None
    run = getattr(scope, "run", scope)
    return run.get(_STALL_SLOT)


def _tools_str(state: _StallState) -> str:
    return ", ".join(state.recent_error_tools) if state.recent_error_tools else "unknown"


def _critical_note(state: _StallState, scope: object) -> str:
    return (
        f"[loop: CRITICAL: {state.consecutive_error_turns} consecutive rounds "
        f"have failed (tools: {_tools_str(state)}); the failing approach is not "
        "making progress. Remaining options: a completely different strategy, or "
        f"{finish_move(scope, answer='best partial answer')}, saying what could "
        "not be done. Repeating the same failing tool calls will fail again.]"
    )


def _warning_note(state: _StallState, scope: object) -> str:
    return (
        f"[loop: Warning: {state.consecutive_error_turns} consecutive rounds had "
        f"failing tool calls (tools: {_tools_str(state)}), which looks like a retry "
        "loop. Options: a fundamentally different approach, working with the "
        f"partial results already gathered, or {finish_move(scope, answer='answer so far')}.]"
    )


def _deliver(ctx: ModelCallContext, note: str) -> str:
    """Put `note` on the latest tool result's loop footer (this call's copy
    only), the channel `warn_before_deadline` uses: an instruction posing as
    a user message right after tool output reads as a prompt attack to
    provider content filters, which reject the whole request. With no tool
    result to carry it, it falls back to an injected user message."""
    if ctx.messages and isinstance(ctx.messages[-1], ToolMessage):
        last = ctx.messages[-1]
        ctx.messages[-1] = last.model_copy(update={"step_footer": last.step_footer + "\n" + note})
        return "step_footer"
    ctx.messages.append(UserMessage(content=note, injected=True))
    return "user_message"


def _is_error_heavy(turn, error_ratio: float) -> bool:
    """A turn is error-heavy if >= error_ratio of its tool results are errors."""
    if turn is None or not turn.tool_results:
        return False
    errors = sum(1 for tr in turn.tool_results if tr.is_error)
    return errors / len(turn.tool_results) >= error_ratio


def stall_detection(
    *,
    warn_after: int = 3,
    fail_after: int = 6,
    error_ratio: float = 0.5,
):
    """Returns a (post_turn_mw, pre_model_mw) pair to register on
    POST_TURN and PRE_MODEL respectively.

    POST_TURN tracks consecutive error-heavy turns.
    PRE_MODEL injects a warning or hard directive based on thresholds.
    """

    def _post_turn(ctx: TurnContext, next_fn):
        """Track consecutive error-heavy turns after each turn completes."""

        async def _inner():
            state = _get_state(ctx.scope)
            if state is not None and ctx.turn is not None:
                if _is_error_heavy(ctx.turn, error_ratio):
                    state.consecutive_error_turns += 1
                    error_names = [
                        tr.name for tr in ctx.turn.tool_results if tr.is_error
                    ]
                    state.total_error_calls += len(error_names)
                    state.recent_error_tools = error_names[-5:]
                else:
                    state.consecutive_error_turns = 0
                    state.warned = False

            await next_fn()

        return _inner()

    def _pre_model(ctx: ModelCallContext, next_fn):
        """Add a warning or a hard directive based on accumulated error state."""

        async def _inner():
            state = _get_state(ctx.scope)
            if state is not None:
                note: str | None = None
                level = ""
                if state.consecutive_error_turns >= fail_after:
                    note, level = _critical_note(state, ctx.scope), "critical"
                elif state.consecutive_error_turns >= warn_after and not state.warned:
                    state.warned = True
                    note, level = _warning_note(state, ctx.scope), "warning"
                if note is not None:
                    channel = _deliver(ctx, note)
                    logger.warning(
                        "stall_detection: note delivered level=%s channel=%s "
                        "consecutive_error_turns=%d session_id=%s",
                        level, channel, state.consecutive_error_turns,
                        getattr(getattr(ctx.scope, "run", None), "session_id", None),
                    )

            await next_fn()

        return _inner()

    return _post_turn, _pre_model
