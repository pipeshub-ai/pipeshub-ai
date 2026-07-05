"""`PipesHubToolLoader`: bulk-registers every applicable PipesHub tool
(registry-backed connector actions + per-request dynamic tools) into an
agent-loop `ToolRegistry`.

Reuses `tool_system.py::get_agent_tools_with_schemas` — the single existing
source of truth for "which tools apply to this request" (blocked-tool
tracking, `has_knowledge` gating, SQL/Slack/web-search dynamic tool
factories, `fetch_full_record` registration) — instead of reimplementing
that selection logic here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.tool_adapter import (
    PipesHubStructuredToolAdapter,
    PipesHubToolAdapter,
    split_original_tool_name,
)
from app.modules.agents.qna.tool_system import get_agent_tools_with_schemas

if TYPE_CHECKING:
    from langchain_core.tools import StructuredTool

    from app.agent_loop_lib.tools.base import Tool
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)


class PipesHubToolLoader:
    """Registers PipesHub tools into an agent-loop `ToolRegistry` per request."""

    def load(self, context: AgentContext, *, skip_apps: set[str] | None = None) -> ToolRegistry:
        """`skip_apps`, when given, excludes every tool whose registry
        `app_name` is in the set — used to drop the legacy
        `coding_sandbox.execute_python`/`execute_typescript` tools once
        `agent_loop_lib`'s own `run_code` is registered (see
        `PipesHubAgentFactory.create`), so the model never sees two
        competing code-execution tools. Defaults to empty so every existing
        call site keeps registering the full tool set unchanged."""
        registry = ToolRegistry()
        skip_apps = skip_apps or set()

        source_tools = get_agent_tools_with_schemas(context.tool_state)
        skipped = 0
        for structured_tool in source_tools:
            wrapper = getattr(structured_tool, "_tool_wrapper", None)
            if wrapper is not None and wrapper.app_name in skip_apps:
                skipped += 1
                continue
            adapter = self._build_adapter(structured_tool, context)
            try:
                registry.register_tool(adapter)
            except (DuplicateToolPathError, DuplicateToolNameError):
                logger.warning("Skipping duplicate tool registration: %s", adapter.name)

        logger.info(
            "PipesHubToolLoader.load: %d source tool(s) -> %d registered "
            "(%d skipped via skip_apps=%s)",
            len(source_tools), len(registry.names()), skipped, sorted(skip_apps),
        )
        return registry

    @staticmethod
    def _build_adapter(structured_tool: StructuredTool, context: AgentContext) -> Tool:
        wrapper = getattr(structured_tool, "_tool_wrapper", None)
        if wrapper is not None:
            return PipesHubToolAdapter(
                wrapper.registry_tool,
                wrapper.app_name,
                wrapper.tool_name,
                context_ref=lambda: context,
            )
        app_name, tool_name = split_original_tool_name(structured_tool)
        return PipesHubStructuredToolAdapter(structured_tool, app_name, tool_name)


__all__ = ["PipesHubToolLoader"]
