"""Legacy internal-knowledge search tool.

Kept so conversations and agents that still name
``retrieval__search_internal_knowledge`` resolve. It is a thin wrapper over
``knowledge_graph.ops.search.execute_search``: there is one search pipeline,
and this tool only keeps its old name and ``connector_ids`` parameter.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.knowledge_graph.ops.results import (
    QUERY_PARAM_DESCRIPTION,
    search_result_summary,
)
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.context.source_catalog import SourceCatalog
from app.modules.agents.qna.chat_state import ChatState

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part

logger = logging.getLogger(__name__)

# connector_id → human label, populated per-request by Retrieval.__init__.
# Safe for concurrent requests: UUIDs are globally unique so the same ID
# always maps to the same label regardless of which request writes it.
_SOURCE_LABELS: dict[str, str] = {}


def _search_internal_knowledge_args_summary(args: dict[str, Any]) -> str | None:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return None
    summary = f'Searched for "{query.strip()}"'
    connector_ids = args.get("connector_ids")
    if isinstance(connector_ids, list) and connector_ids:
        resolved = [_SOURCE_LABELS[cid] for cid in connector_ids if cid in _SOURCE_LABELS]
        if resolved:
            summary += f"\nSources: {', '.join(resolved)}"
        else:
            summary += f"\n{len(connector_ids)} source{'s' if len(connector_ids) != 1 else ''}"
    return summary


@ToolsetBuilder("Retrieval")\
    .in_group("Internal Tools")\
    .with_description("Internal knowledge retrieval tool - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .as_essential()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/retrieval.svg"))\
    .build_decorator()

class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: ChatState | None = None, **kwargs) -> None:
        self.state: ChatState | None = state or kwargs.get('state')
        self._populate_source_labels()

    def _populate_source_labels(self) -> None:
        """Populate module-level source-ID → label mapping for args summaries."""
        if not self.state:
            return
        try:
            catalog = SourceCatalog.from_state(self.state)
            for source in catalog.sources:
                if source.source_id and source.label:
                    _SOURCE_LABELS[source.source_id] = source.label
        except Exception:
            pass

    @tool(
        path="/tools/retrieval/search_internal_knowledge",
        short_description="Search internal knowledge bases and connected data sources",
        description=(
            "Search and retrieve information from indexed company documents, knowledge "
            "bases, and connected data sources. Returns content chunks with citations.\n\n"
            "For a topic that also has a live-API search tool (e.g. Jira, Confluence), "
            "call BOTH this and that tool together — this covers historical/cross-service "
            "content the live API snapshot may not have; a service name in the query "
            "narrows which live tool to also call, it does not replace this call.\n\n"
            "Pass a single `connector_ids` value per call and run one call per source "
            "in parallel. Use the source IDs from the Knowledge Sources section of the "
            "system prompt. Omit `connector_ids` to search all accessible sources."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description=QUERY_PARAM_DESCRIPTION, required=True),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, description="Filter to a specific source by its ID. Pass a single ID per call — use one call per source and run them in parallel. Pass a KB collection's id or an app connector's id — both use this same parameter. Omit to search all accessible sources.", required=False, items={"type": "string"}),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_search_internal_knowledge_args_summary,
        result_summary=search_result_summary,
        display_name="Searched the knowledge base",
    )
    async def search_internal_knowledge(
        self,
        query: str | None = None,
        connector_ids: list[str] | None = None,
    ) -> "str | list[Part]":
        """Search internal knowledge bases and return formatted results."""
        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Retrieval tool state not initialized",
            })
        return await execute_search(self.state, query, source_ids=connector_ids)
