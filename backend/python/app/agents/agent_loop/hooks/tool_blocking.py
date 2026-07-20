"""`ToolErrorTracker`: replaces `_get_blocked_tools`'s consecutive-failure
tracking (`app/modules/agents/qna/tool_system.py`) for the agent-loop path.

The legacy implementation blocks a tool by excluding it from the NEXT
`_load_all_tools()` call — a fresh `ToolRegistry` per planner iteration made
that natural. agent-loop builds `ToolRegistry` once per request
(`PipesHubToolLoader.load()`, Phase 3) and doesn't register toolset groups
for PipesHub tools, so `RunScope.visible_tools` (the plan's original
`ctx.scope.turn.run.visible_tools.discard(...)` sketch) is never consulted
by `agent_loop.agent.tool_loop.tool_schemas_for_turn` in that configuration
— every registered tool's schema is resent to the model every turn
regardless of `visible_tools`. A PRE_TOOL_USE `deny()` achieves the same
practical outcome (the tool stops executing and the model is told why) and
works regardless of toolset-group configuration, so that's the mechanism
used here instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ToolCallContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Next

_DEFAULT_ERROR_THRESHOLD = 3


class ToolErrorTracker:
    """Tracks consecutive failures per tool path for one request/run.

    Register `pre_tool_use` on PRE_TOOL_USE and `post_tool_use` on
    POST_TOOL_USE (same instance, both events) — see
    `app/agents/agent_loop/factory.py`.
    """

    def __init__(self, threshold: int = _DEFAULT_ERROR_THRESHOLD) -> None:
        self._threshold = threshold
        self._consecutive_errors: dict[str, int] = {}
        # Last turn_index that already contributed a failure to a tool's
        # streak — makes `record()` turn-aware instead of call-aware.
        self._last_failed_turn: dict[str, int] = {}

    def is_blocked(self, tool_path: str) -> bool:
        return self._consecutive_errors.get(tool_path, 0) >= self._threshold

    def record(self, tool_path: str, *, is_error: bool, turn_index: int | None = None) -> None:
        """A success resets the streak — only *consecutive* failures count,
        matching the legacy blocked-tools semantics.

        `turn_index`, when given, makes the streak count turns rather than
        calls. Parallel tool calls within one turn are gathered concurrently
        (see `Agent.step()`), so several calls to the same tool in one turn
        fail or succeed together from the model's perspective — that's one
        attempt fanned out, not `N` independent "tries in a row". Without
        this guard, a single turn issuing `threshold` parallel calls to a
        flaky tool would block it after ONE turn instead of `threshold`
        separate turns.
        """
        if is_error:
            if turn_index is not None and self._last_failed_turn.get(tool_path) == turn_index:
                return
            self._consecutive_errors[tool_path] = self._consecutive_errors.get(tool_path, 0) + 1
            if turn_index is not None:
                self._last_failed_turn[tool_path] = turn_index
        else:
            self._consecutive_errors.pop(tool_path, None)
            self._last_failed_turn.pop(tool_path, None)

    async def pre_tool_use(self, ctx: ToolCallContext, next_fn: Next) -> None:
        if self.is_blocked(ctx.tool_path):
            ctx.deny(
                f"Tool at {ctx.tool_path!r} has failed {self._threshold}+ times in a row this "
                "conversation and has been temporarily disabled. Try a different tool or approach."
            )
            return
        await next_fn()

    async def post_tool_use(self, ctx: ToolResultContext, next_fn: Next) -> None:
        await next_fn()
        turn_index = ctx.scope.turn.turn_index if ctx.scope is not None else None
        self.record(ctx.tool_path, is_error=not ctx.tool_response.success, turn_index=turn_index)


__all__ = ["ToolErrorTracker"]
