"""Knowledge-routing context builder for the agent-loop prompt.

This module emits the source catalog — ``catalog.render()`` embeds the
record model, the tool-per-level map, and the duplicate-connector note
directly inside the rendered text.  All surface-arbitration logic (source
precedence, parallelism, search depth) is owned by ``PipesHubPromptBuilder``'s
``finding_information`` section, which has access to ``ToolSurfaces`` and can
gate every rule on what is actually granted.
"""


from __future__ import annotations

import logging
from typing import Any

from app.modules.agents.context.source_catalog import SourceCatalog
from app.modules.agents.qna.chat_state import ChatState


def _build_knowledge_context(
    state: ChatState,
    log: logging.Logger,
    catalog: SourceCatalog | None = None,
    *,
    tool_names: list[str] | None = None,
) -> str:
    """Build the knowledge & data sources block for the agent-loop system prompt.

    IDs appear exactly once — inside ``catalog.render()``.  The record model
    and tool-per-level map are also rendered inside ``catalog.render()`` (only
    when ``ids_actionable`` is True — i.e. on the agent route where
    ``agent_knowledge`` is populated).

    Pass a pre-built ``catalog`` (from ``AgentContext.get_source_catalog()``)
    to avoid re-walking ``agent_knowledge`` when the caller already has one.

    Pass ``tool_names`` (from ``spec.tool_names``) so that tool references
    in the rendered text use the exact granted spellings the model can call.
    """
    del log  # reserved for future diagnostics; keep signature stable
    resolved = catalog if catalog is not None else SourceCatalog.from_state(state)
    if resolved.is_empty():
        return ""
    return resolved.render(tool_names=tool_names)
