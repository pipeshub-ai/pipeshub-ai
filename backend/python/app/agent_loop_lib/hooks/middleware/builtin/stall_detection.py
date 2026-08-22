"""Stall detection: a POST_TURN + PRE_MODEL middleware pair that detects
when an agent is making no forward progress (repeated error-heavy turns)
and intervenes — first with a warning message injected into context, then
by escalating the warning to a hard "stop retrying" directive.

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

from dataclasses import dataclass, field

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext, TurnContext

__all__ = ["stall_detection"]


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
        """Inject warning or hard directive based on accumulated error state."""

        async def _inner():
            state = _get_state(ctx.scope)
            if state is not None:
                if state.consecutive_error_turns >= fail_after:
                    tools_str = ", ".join(state.recent_error_tools) if state.recent_error_tools else "unknown"
                    ctx.messages.append(UserMessage(
                        content=(
                            f"[System: CRITICAL — {state.consecutive_error_turns} consecutive turns "
                            f"have failed (tools: {tools_str}). You are not making progress. "
                            "You MUST stop retrying the failing approach. Either:\n"
                            "1. Try a completely different strategy, OR\n"
                            "2. Call task_complete with your best partial answer and explain what "
                            "you were unable to accomplish.\n"
                            "Do NOT repeat the same failing tool calls.]"
                        ),
                    ))
                elif state.consecutive_error_turns >= warn_after and not state.warned:
                    state.warned = True
                    tools_str = ", ".join(state.recent_error_tools) if state.recent_error_tools else "unknown"
                    ctx.messages.append(UserMessage(
                        content=(
                            f"[System: Warning — {state.consecutive_error_turns} consecutive turns "
                            f"have had failing tool calls (tools: {tools_str}). "
                            "You may be stuck in a retry loop. Consider:\n"
                            "1. A fundamentally different approach to the task\n"
                            "2. Working with the partial results you already have\n"
                            "3. Calling task_complete with what you have so far\n"
                            "Do not keep retrying the same failing approach.]"
                        ),
                    ))

            await next_fn()

        return _inner()

    return _post_turn, _pre_model
