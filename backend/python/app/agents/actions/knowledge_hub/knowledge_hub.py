"""
Knowledge Hub Internal Tool

Allows agents to browse and search files, folders, and knowledge bases
in the Knowledge Hub, automatically scoped to the agent's configured
knowledge sources. Complements the retrieval tool: this browses file
metadata/structure, while retrieval searches file contents.
"""

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
    NodeType,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.models.entities import RecordType
from app.modules.agents.qna.chat_state import (
    ChatState,
    _extract_kb_record_groups,
    _extract_knowledge_connector_ids,
)

logger = logging.getLogger(__name__)

# Valid values for input validation
_VALID_NODE_TYPES = {nt.value for nt in NodeType}
_VALID_SORT_FIELDS = {"name", "createdAt", "updatedAt", "size", "type"}
_VALID_SORT_ORDERS = {"asc", "desc"}

# Build description fragments from enums so they stay in sync automatically
_NODE_TYPES_DESC = ", ".join(f"'{nt.value}'" for nt in NodeType)
_RECORD_TYPES_DESC = ", ".join(f"'{rt.value}'" for rt in RecordType)

# Query validation constants
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 500


class ListFilesInput(BaseModel):
    """Input schema for the list_files tool"""
    query: str | None = Field(
        default=None,
        description=(
            "Search query to find files by name (2-500 chars). "
            "Leave empty to browse without searching."
        ),
    )
    parent_id: str | None = Field(
        default=None,
        description=(
            "ID of the node to browse into. Get the ID from the capability summary "
            "or from a previous list_files response. Required for browsing."
        ),
    )
    parent_type: str | None = Field(
        default=None,
        description=(
            "Type of the parent node. Required when parent_id is provided. "
            "Values: 'app' (connector root), 'recordGroup' (KB / space / drive), "
            "'folder' (subfolder), 'record' (file with children)."
        ),
    )
    node_types: list[str] | None = Field(
        default=None,
        description=(
            f"Filter results by node type. "
            f"All valid values: {_NODE_TYPES_DESC}. "
            f"Example: ['record'] for files only, "
            f"['folder', 'recordGroup'] for containers only."
        ),
    )
    connector_ids: list[str] | None = Field(
        default=None,
        description=(
            "Filter results to specific connectors by their IDs. "
            "Get the connector ID from the capability summary. "
            "Only needed for search (query). Not needed when browsing with parent_id."
        ),
    )
    record_group_ids: list[str] | None = Field(
        default=None,
        description=(
            "Filter search results to specific KB collections by their record group IDs. "
            "Get IDs from the capability summary. Only applies to Collection/KB sources. "
            "For connector spaces (Confluence, Drive, etc.), use parent_id with "
            "parent_type='recordGroup' to browse into a specific space instead."
        ),
    )
    record_types: list[str] | None = Field(
        default=None,
        description=(
            f"Filter by record type (only applies to 'record' nodeType). "
            f"All valid values: {_RECORD_TYPES_DESC}. "
            f"Example: ['CONFLUENCE_PAGE'] for Confluence pages only, "
            f"['FILE'] for uploaded files only."
        ),
    )
    only_containers: bool = Field(
        default=False,
        description="If true, only return containers (folders, KBs, apps) that have children.",
    )
    page: int = Field(
        default=1,
        description="Page number for pagination (starts at 1).",
    )
    limit: int = Field(
        default=20,
        description="Number of items per page (1-50).",
    )
    sort_by: str = Field(
        default="updatedAt",
        description="Sort field: 'name', 'createdAt', 'updatedAt', 'size', 'type'.",
    )
    sort_order: str = Field(
        default="desc",
        description="Sort order: 'asc' or 'desc'.",
    )
    flattened: bool = Field(
        default=False,
        description="If true, return all nested items recursively instead of direct children only.",
    )


def _normalize_list_param(value: str | list[object] | None) -> list[str] | None:
    """Normalize a parameter that should be a list of strings.
    Handles LLM sending a single string instead of a list, or empty list."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else None
    if isinstance(value, list):
        filtered = [str(v) for v in value if v]
        return filtered if filtered else None
    return None


def _format_browse_response(response: KnowledgeHubNodesResponse) -> str:
    """Format KnowledgeHubNodesResponse into compact JSON for LLM consumption."""
    if not response.success:
        return json.dumps({
            "status": "error",
            "message": response.error or "Failed to browse knowledge files",
        })

    items = []
    for item in response.items:
        node: dict[str, Any] = {
            "id": item.id,
            "name": item.name,
            "nodeType": item.nodeType,
            "hasChildren": item.hasChildren,
        }
        # Include optional fields only when present to keep response compact
        if item.recordType:
            node["recordType"] = item.recordType
        if item.sizeInBytes is not None:
            node["sizeInBytes"] = item.sizeInBytes
        if item.webUrl:
            node["webUrl"] = item.webUrl
        if item.mimeType:
            node["mimeType"] = item.mimeType
        if item.extension:
            node["extension"] = item.extension
        if item.updatedAt:
            node["updatedAt"] = item.updatedAt
        if item.createdAt:
            node["createdAt"] = item.createdAt
        if item.connector:
            node["connector"] = item.connector
        items.append(node)

    result: dict[str, Any] = {
        "status": "success",
        "items": items,
        "resultCount": len(items),
    }

    if response.pagination:
        result["pagination"] = {
            "page": response.pagination.page,
            "limit": response.pagination.limit,
            "totalItems": response.pagination.totalItems,
            "totalPages": response.pagination.totalPages,
            "hasNext": response.pagination.hasNext,
        }

    if response.currentNode:
        result["currentNode"] = {
            "id": response.currentNode.id,
            "name": response.currentNode.name,
            "nodeType": response.currentNode.nodeType,
        }

    return json.dumps(result, ensure_ascii=False)


@ToolsetBuilder("KnowledgeHub")\
    .in_group("Internal Tools")\
    .with_description("Browse and search files in the Knowledge Hub")\
    .with_category(ToolCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/knowledge_hub.svg"))\
    .build_decorator()
class KnowledgeHub:
    """Knowledge Hub tool for browsing files and folders in the Knowledge Hub"""

    def __init__(self, state: ChatState | None = None) -> None:
        self.state: ChatState | None = state

    @tool(
        app_name="knowledge_hub",
        tool_name="list_files",
        description="List and search all indexed items in the Knowledge Hub by name, structure, or metadata",
        args_schema=ListFilesInput,
        llm_description=(
            "List, browse, and search all items indexed in the Knowledge Hub. "
            "This includes every type of record the system indexes from connected services "
            "(use record_types and node_types parameters to filter).\n\n"
            "BROWSING: Pass parent_id + parent_type to navigate the hierarchy.\n"
            "SEARCHING: Pass query to find items by name across all sources.\n"
            "FILTERING: Use node_types and record_types to narrow results.\n\n"
            "This tool operates on indexed metadata (names, types, dates, structure). "
            "For searching WITHIN document content, use retrieval.search_internal_knowledge."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User wants to list, browse, or find indexed items by name or metadata",
            "User asks what items are available in a knowledge source or connector",
            "User wants to explore the structure or hierarchy of knowledge sources",
        ],
        when_not_to_use=[
            "User wants to search WITHIN document content (use retrieval instead)",
            "User wants to create, update, or delete items",
            "User asks about a topic rather than listing items",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What items are in the knowledge base?",
            "List all indexed items",
            "Find items named 'policy'",
            "Show me the folder structure",
            "What knowledge sources are available?",
        ],
    )
    async def list_files(
        self,
        query: str | None = None,
        parent_id: str | None = None,
        parent_type: str | None = None,
        node_types: list[str] | None = None,
        connector_ids: list[str] | None = None,
        record_group_ids: list[str] | None = None,
        record_types: list[str] | None = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
        flattened: bool = False,
    ) -> str:
        """Browse and search files in the Knowledge Hub."""
        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Knowledge hub tool state not initialized",
            })

        try:
            logger_instance = self.state.get("logger", logger)
            graph_provider = self.state.get("graph_provider")
            org_id = self.state.get("org_id", "")
            user_id = self.state.get("user_id", "")

            if not graph_provider:
                return json.dumps({
                    "status": "error",
                    "message": "Graph provider not available",
                })

            # Extract knowledge scoping from agent configuration.
            agent_knowledge = self.state.get("agent_knowledge") or []
            agent_connector_ids = _extract_knowledge_connector_ids(agent_knowledge)
            kb_ids = set(_extract_kb_record_groups(agent_knowledge))

            if not agent_connector_ids:
                return json.dumps({
                    "status": "error",
                    "message": "No knowledge sources configured for this agent",
                })

            # --- Input normalization ---
            # LLMs often send empty strings instead of null for optional params
            query = query.strip() if query else None
            parent_id = parent_id.strip() if parent_id else None
            parent_type = parent_type.strip() if parent_type else None

            if parent_id and not parent_type:
                return json.dumps({
                    "status": "error",
                    "message": "parent_type is required when parent_id is provided. "
                               "Valid types: 'kb', 'app', 'folder', 'recordGroup'.",
                })

            # Query must be 2-500 chars or None
            if query and len(query) < MIN_QUERY_LENGTH:
                query = None
            elif query and len(query) > MAX_QUERY_LENGTH:
                query = query[:MAX_QUERY_LENGTH]

            # Normalize list params (handle LLM sending string instead of list,
            # or empty list instead of null)
            connector_ids = _normalize_list_param(connector_ids)
            record_group_ids = _normalize_list_param(record_group_ids)
            node_types = _normalize_list_param(node_types)
            record_types = _normalize_list_param(record_types)

            # Filter to valid node types
            if node_types:
                node_types = [nt for nt in node_types if nt in _VALID_NODE_TYPES]
                if not node_types:
                    node_types = None

            # Cap and validate pagination
            page = max(1, page)
            limit = min(max(1, limit), 50)

            # Validate sort fields
            if sort_by not in _VALID_SORT_FIELDS:
                sort_by = "updatedAt"
            if sort_order not in _VALID_SORT_ORDERS:
                sort_order = "desc"

            # --- Execute ---

            logger_instance.info(
                f"Knowledge hub browse: query={query!r}, parent_id={parent_id}, "
                f"parent_type={parent_type}, page={page}, limit={limit}"
            )

            service = KnowledgeHubService(
                logger=logger_instance,
                graph_provider=graph_provider,
            )

            # ── Security boundary: ALWAYS restrict to agent's configured sources ──
            #
            # Both connector_ids and record_group_ids represent the agent's
            # allowed scope. They are ALWAYS passed to the service — no exceptions.
            #
            # The DB-level filters are origin-aware:
            # - connector_ids: scopes by connector (app nodes + their children)
            # - record_group_ids: only filters COLLECTION-origin recordGroups (KBs),
            #   NOT CONNECTOR-origin recordGroups (Confluence spaces, etc.)
            #
            # This means both can be passed simultaneously without interference.
            #
            # For browse mode (parent_id + no query), the service uses tree
            # navigation. Passing connector_ids would trigger scoped search,
            # so we only pass them when searching or when LLM explicitly provides them.

            # connector_ids: agent's real connectors (knowledgeBase_* stripped).
            # May be empty for KB-only agents (only knowledgeBase_* connectors).
            agent_real_connector_ids = [
                cid for cid in agent_connector_ids
                if not cid.startswith("knowledgeBase_")
            ]

            if connector_ids:
                # LLM provided explicit connector_ids — intersect with agent config.
                # If intersection is empty (LLM passed invalid IDs), fall back to
                # full agent config so the search space isn't unnecessarily empty.
                allowed = set(agent_real_connector_ids)
                intersected = [
                    cid for cid in connector_ids
                    if not cid.startswith("knowledgeBase_") and cid in allowed
                ]
                use_connector_ids = intersected if intersected else (agent_real_connector_ids or None)
            elif query:
                # Searching — always scope to agent's configured connectors
                use_connector_ids = agent_real_connector_ids or None
            elif not parent_id:
                # No parent, no query — root browse. Pass connector_ids to
                # scope root-level apps to only configured ones.
                # Include ALL agent connector IDs (even knowledgeBase_*) for
                # root filtering — the root shows apps, and knowledgeBase_*
                # IS a valid app ID in the DB for the KB connector.
                use_connector_ids = list(agent_connector_ids) if agent_connector_ids else None
            else:
                # Browsing with parent_id, no query — tree navigation.
                # Don't pass connector_ids (would trigger scoped search).
                use_connector_ids = None

            # record_group_ids: KB security boundary. ALWAYS from agent config.
            #
            # The DB filter is origin-aware:
            # - COLLECTION recordGroups (KBs): restricted to record_group_ids
            # - CONNECTOR recordGroups (spaces/drives): pass through
            #   (already scoped by connector_ids)
            #
            # LLM can narrow KB results by passing specific KB IDs
            # (intersected with agent config — can't expand).
            # For narrowing connector spaces, use parent_id instead.
            if record_group_ids and kb_ids:
                # LLM wants specific KBs — intersect with agent config
                use_record_group_ids = [
                    rg for rg in record_group_ids if rg in kb_ids
                ] or list(kb_ids)  # fallback to full config if intersection is empty
            else:
                use_record_group_ids = list(kb_ids) if kb_ids else None

            response = await service.get_nodes(
                user_id=user_id,
                org_id=org_id,
                parent_id=parent_id,
                parent_type=parent_type,
                only_containers=only_containers,
                page=page,
                limit=limit,
                sort_by=sort_by,
                sort_order=sort_order,
                q=query,
                node_types=node_types,
                record_types=record_types,
                connector_ids=use_connector_ids,
                flattened=flattened,
                record_group_ids=use_record_group_ids,
            )

            return _format_browse_response(response)

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in knowledge hub tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Knowledge hub error: {str(e)}",
            })
