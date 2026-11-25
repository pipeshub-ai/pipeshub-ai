"""`RepeatedReadGuard`: stops one MCP read from running again and again with the same arguments.

Bug bash: an agent made the same call 14 times in one reply, until the run's turn limit.
`ToolErrorTracker` counts only failures, and `TAG_DEDUP_EXACT` only repeats within one wave.

What it covers, and why:
- MCP read-only tools. A write asks for approval anyway, and a read of another kind of tool (a
  sandbox file, a browser page) can change between identical calls.
- Each run on its own (a `StateSlot`, not inherited): a child agent starts with an empty context
  and may need what a sibling already looked up.
- Only the last few turns: `shape_artifact_compaction` keeps 2 turns and `tool_result_clearing`
  tells the model to call again with the same arguments for a result it dropped.
- A write that succeeds starts the count again: the same read may now return something else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.scope import StateSlot
from app.agents.agent_loop.tool_approvals import TOOLS_BY_PATH
from harness_core.effects.types import args_digest

if TYPE_CHECKING:
    from app.agent_loop_lib.core.scope import RunScope
    from app.agent_loop_lib.hooks.middleware.context import (
        ToolCallContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Next
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter

# Refused once the same read already ran this many times within the recent turns.
_MAX_RUNS = 2
# The turns before this one whose results the model can still see in full.
_RECENT_TURNS = 2
_KEY = "repeated_reads.key"


@dataclass
class _Runs:
    # Call key → the turns it was called in: ran successfully, or refused here (a model that keeps
    # calling hasn't moved on, so a refusal must not let the next attempt through).
    turns: dict[str, list[int]] = field(default_factory=dict)
    refused: dict[str, int] = field(default_factory=dict)


_RUNS: StateSlot[_Runs] = StateSlot(key="repeated_reads.runs", default_factory=_Runs)


def _call_key(tool_path: str, arguments: dict[str, Any]) -> str:
    return _digest_key(tool_path, _arguments_digest(arguments))


def _digest_key(tool_path: str, digest: str) -> str:
    return f"{tool_path}\n{digest}"


def _arguments_digest(arguments: dict[str, Any]) -> str:
    # The kernel's own digest, so a resumed durable attempt rebuilds the same keys from the journal.
    try:
        return args_digest(arguments)
    except (TypeError, ValueError):
        return json.dumps(arguments, sort_keys=True, default=str)


def _refused(runs: _Runs, key: str, turn: int) -> int:
    """How many times this call was refused, counting this one; 0 when it may run."""
    if sum(1 for t in runs.turns.get(key, ()) if t >= turn - _RECENT_TURNS) < _MAX_RUNS:
        return 0
    runs.turns[key].append(turn)
    runs.refused[key] = runs.refused.get(key, 0) + 1
    return runs.refused[key]


def _refusal(tool: str, times: int) -> str:
    if times > 1:
        return (
            f"Not run again: {tool} keeps being called with the same arguments and its result doesn't change. "
            "Stop calling it and answer the user now with what you have."
        )
    return (
        f"Not run: {tool} already ran {_MAX_RUNS} times in the last few steps with exactly these arguments, "
        "and nothing has changed since. Use its result above, call it with different arguments, or answer the "
        "user with what you have."
    )


class RepeatedReadGuard:
    """Register `pre_tool_use` on PRE_TOOL_USE and `post_tool_use` on POST_TOOL_USE (same instance)."""

    def __init__(self, context: "AgentContext") -> None:
        self._context = context

    def _adapter(self, tool_path: str) -> "MCPToolAdapter | None":
        return self._context.tool_state.get(TOOLS_BY_PATH, {}).get(tool_path)

    async def pre_tool_use(self, ctx: "ToolCallContext", next_fn: "Next") -> None:
        adapter = self._adapter(ctx.tool_path)
        # No scope: the approved call an answer runs (`tool_approvals._run`), outside any turn.
        if ctx.scope is None or adapter is None or adapter.kind != "read":
            await next_fn()
            return
        runs: _Runs = ctx.scope.turn.run.get(_RUNS)
        key = _call_key(ctx.tool_path, ctx.tool_input)
        times = _refused(runs, key, ctx.scope.turn.turn_index)
        if times:
            ctx.deny(_refusal(adapter.display_name, times))
            return
        ctx.metadata[_KEY] = key
        await next_fn()

    async def post_tool_use(self, ctx: "ToolResultContext", next_fn: "Next") -> None:
        await next_fn()
        if ctx.scope is None or not ctx.tool_response.success:
            return
        run: "RunScope" = ctx.scope.turn.run
        key = ctx.metadata.get(_KEY)
        if key is not None:
            run.get(_RUNS).turns.setdefault(key, []).append(ctx.scope.turn.turn_index)
            return
        adapter = self._adapter(ctx.tool_path)
        if adapter is not None and adapter.kind != "read":
            run.set(_RUNS, _Runs())

    def replay(self, run: "RunScope", tool_path: str, digest: str, turn_index: int, *, ok: bool) -> None:
        """One journaled call of a durable run, as `pre_tool_use` and `post_tool_use` saw it, so a resumed
        attempt starts with the memory an uninterrupted run has (whether it was refused is recomputed)."""
        adapter = self._adapter(tool_path)
        if adapter is None:
            return
        if adapter.kind != "read":
            if ok:
                run.set(_RUNS, _Runs())
            return
        runs: _Runs = run.get(_RUNS)
        key = _digest_key(tool_path, digest)
        if not _refused(runs, key, turn_index) and ok:
            runs.turns.setdefault(key, []).append(turn_index)


__all__ = ["RepeatedReadGuard"]
