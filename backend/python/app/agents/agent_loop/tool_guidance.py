"""`ToolGuidanceProvider`: connector-level guidance blocks (Layer 2 of
PipesHub's three-layer prompt system) for injection by Phase 4's
`PipesHubPromptBuilder`.

Per-tool guidance (Layer 1 — `llm_description`/`when_to_use`/
`when_not_to_use`) is carried directly on `PipesHubToolAdapter.description`
(`tool_adapter.py`), which agent-loop already includes in the tool schemas
sent to the LLM — so this provider only surfaces the connector-wide
`*_GUIDANCE` blocks extracted from `nodes.py` in Phase 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.modules.agents.context.connector_detection import (
    derive_active_connectors,
    has_connector,
)
from app.modules.agents.prompts.connector_guidance import GUIDANCE_MAP

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext


class ToolGuidanceProvider:
    """Provides connector-level guidance blocks for prompt injection."""

    def get_active_guidance(self, context: AgentContext) -> dict[str, str]:
        """Returns `{connector_name: guidance_text}` for the connectors
        present in `context.agent_toolsets`.

        Uses the same substring-match semantics as the legacy per-connector
        `_has_<connector>_tools()` helpers (`connector_detection.py`) — e.g.
        a toolset named "jira-cloud" still counts as Jira — so a request
        that would have triggered `JIRA_GUIDANCE` on the LangGraph path
        triggers it here too.
        """
        active_connectors = derive_active_connectors(context.agent_toolsets)
        return {
            name: guidance
            for name, guidance in GUIDANCE_MAP.items()
            if has_connector(active_connectors, name)
        }


__all__ = ["ToolGuidanceProvider"]
