"""`retrieval_context_emission`: POST_TOOL_USE hook that streams a
`retrieval_context` frame for whatever a tool call added to the model's
context. Registered only when the request opted in
(`AgentContext.include_retrieval_context`), so the default path pays nothing.

Deliberately tool-agnostic — it diffs shared `tool_state` after every call
via `RetrievalContextLedger`, so search, fetch, navigate and any future
retrieval tool are covered without a tool-name list. Sub-agents share the
parent's hook registry, so their tool calls are reported too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.agent_loop.hooks._tool_naming import resolve_tool_name
from app.agents.agent_loop.retrieval_ledger import emit_retrieval_context

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext


def retrieval_context_emission(context: AgentContext) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook factory closing over the per-request `AgentContext`."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        output = ctx.tool_response
        await emit_retrieval_context(
            context,
            source="tool",
            status="ok" if output.success else "error",
            tool_name=resolve_tool_name(ctx),
            tool_call_id=str(ctx.tool_use_id),
            error_message=None if output.success else str(output.error or ""),
        )

    return _middleware


__all__ = ["retrieval_context_emission"]
