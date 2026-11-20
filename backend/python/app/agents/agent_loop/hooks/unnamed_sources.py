"""POST_TOOL_USE: in a run others read, a tool's result names no source the run's prompts don't name.

A search hit, a fetched record or a looked-up one carries its source in its header: the first segment of its Location
trail and its Connector ID. For a source the turn didn't pick, both are written as "another source" before any other
middleware (the artifact store among them) or the model sees the result. The record itself stays, named and labelled
as before. The tools that list sources (navigate, list_files, the entity tools) leave unnamed ones out themselves.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import TextPart
from app.modules.agents.context.named_sources import (
    nameable_source_ids,
    without_unnamed_sources,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext


def _rewritten(data: Any, nameable: frozenset[str]) -> Any:  # noqa: ANN401
    if isinstance(data, str):
        return without_unnamed_sources(data, nameable)
    if isinstance(data, list):
        return [
            TextPart(text=without_unnamed_sources(part.text, nameable)) if isinstance(part, TextPart) else
            without_unnamed_sources(part, nameable) if isinstance(part, str) else part
            for part in data
        ]
    return data


def keep_unnamed_sources_out(context: AgentContext) -> Middleware[ToolResultContext] | None:
    """The middleware, or None for a run whose results may name any source."""
    nameable = nameable_source_ids(context.tool_state)
    if nameable is None:
        return None

    async def _middleware(ctx: ToolResultContext, next_fn: Next) -> None:
        response = ctx.tool_response
        data = _rewritten(response.data, nameable)
        error = without_unnamed_sources(response.error, nameable) if isinstance(response.error, str) else response.error
        if data != response.data or error != response.error:
            ctx.tool_response = replace(response, data=data, error=error)
        await next_fn()

    return _middleware


__all__ = ["keep_unnamed_sources_out"]
