"""Shared helper for Phase 5 hooks that need the LLM-facing tool name
(e.g. ``jira__search_issues``) a ``ToolResultContext``/``ToolCallContext``
corresponds to, rather than its registry path (``/tools/jira/search_issues``).
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


# The composed delegate name (`domain_agents.py`) and the flat tool name it
# claims when domain-agent composition is disabled — an agent's OWN
# `spec.tool_names` containing either one means that agent has an
# internal-search surface available this turn. Shared by
# `knowledge_first_gate.py` (deciding whether to nudge) and `citations.py`
# (deciding whether the top-level agent is allowed to also be granted
# `dynamic_fetch_full_record`) so the two hooks agree on what "has internal
# search" means without either re-deriving it.
INTERNAL_SEARCH_DELEGATE_NAME = "internal_exploration_agent"
INTERNAL_SEARCH_FLAT_NAME = "retrieval_search_internal_knowledge"
INTERNAL_SEARCH_TOOL_NAMES = frozenset({INTERNAL_SEARCH_DELEGATE_NAME, INTERNAL_SEARCH_FLAT_NAME})


__all__ = [
    "INTERNAL_SEARCH_DELEGATE_NAME",
    "INTERNAL_SEARCH_FLAT_NAME",
    "INTERNAL_SEARCH_TOOL_NAMES",
    "resolve_tool_name",
]
