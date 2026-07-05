"""`result_accumulation`: PRE/POST_TOOL_USE hook pair that appends one entry
per tool call to `AgentContext.tool_state["all_tool_results"]`, in the exact
shape `nodes.py::_execute_single_tool` returns (`tool_name`, `result`,
`status`, `tool_id`, `args`, `duration_ms`) — Phase 6's `RespondPipeline`
reads this list unchanged (see e.g. `nodes.py` lines 2565/3033/3168/4123),
so reproducing the shape here (rather than inventing a new one) means no
changes are needed downstream.

Split across two hooks because duration and status are only known after
execution, but `tool_input`/start time must be captured before it — the
PRE hook stashes both into `ctx.metadata`, which the executor carries
forward onto the matching `ToolResultContext` via the shared `tool_use_id`
(see `hooks/middleware/context.py` docstring).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ToolCallContext,
        ToolResultContext,
    )
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext


async def stash_tool_call_metadata(ctx: ToolCallContext, next_fn: "Next") -> None:
    """PRE_TOOL_USE: records start time + args for the matching POST hook."""
    ctx.metadata["_result_accum_started_at"] = time.perf_counter()
    ctx.metadata["_result_accum_args"] = dict(ctx.tool_input)
    await next_fn()


def result_accumulation(context: AgentContext) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        started_at = ctx.metadata.get("_result_accum_started_at")
        duration_ms = (time.perf_counter() - started_at) * 1000 if started_at is not None else 0.0
        output = ctx.tool_response

        tool_name = resolve_tool_name(ctx)
        entry: dict[str, Any] = {
            "tool_name": tool_name,
            "result": output.data if output.success else f"Error: {output.error}",
            "status": "success" if output.success else "error",
            "tool_id": str(ctx.tool_use_id),
            "args": ctx.metadata.get("_result_accum_args", {}),
            "duration_ms": duration_ms,
        }
        context.tool_state.setdefault("all_tool_results", []).append(entry)

    return _middleware


__all__ = ["stash_tool_call_metadata", "result_accumulation"]
