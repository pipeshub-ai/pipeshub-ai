"""Shared helper for Phase 5 hooks that need the LLM-facing short tool name
(`jira_search_issues`) a `ToolResultContext`/`ToolCallContext` corresponds
to, rather than its registry path (`/connectors/jira/search_issues`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import (
        ToolCallContext,
        ToolResultContext,
    )


def resolve_tool_name(ctx: "ToolCallContext | ToolResultContext") -> str:
    """Resolves through the registry rather than re-deriving the name from
    the path string, so this stays correct if path structure ever changes."""
    registry = ctx.scope.turn.run.runtime.tool_registry if ctx.scope is not None else None
    if registry is not None and registry.has_path(ctx.tool_path):
        return registry.resolve(ctx.tool_path).name
    segments = [s for s in ctx.tool_path.split("/") if s]
    return "_".join(segments[-2:]) if len(segments) >= 2 else ctx.tool_path


__all__ = ["resolve_tool_name"]
