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

import hashlib
import json
from dataclasses import dataclass, field

from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext, TurnContext

__all__ = ["STALL_STOP_MARKER", "STEP_BACK_MARKER", "doom_loop_detection", "stall_detection"]

#: Prefix of the run's error when doom-loop detection stops it; matched by
#: `app/agents/agent_loop/error_classification.py` to pick the user message.
STALL_STOP_MARKER = "[agent_loop_stopped:repeated_calls]"
STEP_BACK_MARKER = "[System: Step back"


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


@dataclass
class _DoomLoopState:
    last_signature: str | None = None
    repeats: int = 0
    stepped_back: bool = False
    pending_step_back: bool = False
    stop_reason: str | None = None


_DOOM_LOOP_SLOT: StateSlot[_DoomLoopState] = StateSlot(
    key="doom_loop_detection.state",
    default_factory=_DoomLoopState,
)


def _turn_signature(turn) -> str | None:
    """Identity of a turn's tool activity: every call (name + arguments) and
    what it returned. A call repeated with a different result (polling,
    paging) is progress, not a loop, so results are part of the key."""
    if turn is None or not turn.tool_calls:
        return None
    results = {tr.tool_call_id: tr for tr in turn.tool_results}
    entries = []
    for call in turn.tool_calls:
        tr = results.get(call.id)
        content = "" if tr is None else str(tr.content)
        entries.append([
            call.name,
            json.dumps(call.arguments, sort_keys=True, default=str),
            None if tr is None else tr.is_error,
            hashlib.sha256(content.encode()).hexdigest(),
        ])
    return json.dumps(sorted(entries))


def doom_loop_detection(*, repeat_threshold: int = 3):
    """Returns `(post_turn_mw, pre_model_mw, pre_turn_mw)`.

    After `repeat_threshold` consecutive turns with identical tool calls and
    identical results, the next model call gets one "step back" message. If
    the agent repeats the loop again after that, the next turn is denied, so
    the run ends with an error starting with `STALL_STOP_MARKER` instead of
    burning the remaining turns.
    """

    def _post_turn(ctx: TurnContext, next_fn):
        async def _inner():
            state = _get_doom_state(ctx.scope)
            if state is not None and ctx.turn is not None:
                signature = _turn_signature(ctx.turn)
                if signature is not None and signature == state.last_signature:
                    state.repeats += 1
                else:
                    state.repeats = 1 if signature is not None else 0
                state.last_signature = signature
                if state.repeats >= repeat_threshold:
                    if not state.stepped_back:
                        state.stepped_back = True
                        state.pending_step_back = True
                    else:
                        names = sorted({c.name for c in ctx.turn.tool_calls})
                        state.stop_reason = (
                            f"{STALL_STOP_MARKER} repeated the same tool call(s) "
                            f"{', '.join(names)} {state.repeats} turns in a row after being asked to step back"
                        )
            await next_fn()

        return _inner()

    def _pre_model(ctx: ModelCallContext, next_fn):
        async def _inner():
            state = _get_doom_state(ctx.scope)
            if state is not None and state.pending_step_back:
                state.pending_step_back = False
                ctx.messages.append(UserMessage(content=(
                    f"{STEP_BACK_MARKER} — your last {state.repeats} turns made the exact same "
                    "tool call(s) and got the exact same result(s). Repeating them again will not "
                    "change anything. Use what those results already told you: either answer now "
                    "with what you have, or try a genuinely different tool or different arguments. "
                    "If you repeat the same call again, the run will be stopped.]"
                )))
            await next_fn()

        return _inner()

    def _pre_turn(ctx: TurnContext, next_fn):
        async def _inner():
            state = _get_doom_state(ctx.scope)
            if state is not None and state.stop_reason:
                ctx.deny(state.stop_reason)
                return
            await next_fn()

        return _inner()

    return _post_turn, _pre_model, _pre_turn


def _get_doom_state(scope) -> _DoomLoopState | None:
    if scope is None:
        return None
    run = getattr(scope, "run", scope)
    return run.get(_DOOM_LOOP_SLOT)
