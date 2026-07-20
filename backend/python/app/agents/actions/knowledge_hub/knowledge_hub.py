"""
Knowledge Hub Internal Tool

Allows agents to browse and search files, folders, and knowledge bases
in the Knowledge Hub, automatically scoped to the agent's configured
knowledge sources. Complements the retrieval tool: this browses file
metadata/structure, while retrieval searches file contents.
"""

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import bullet_list, parse_json_maybe
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
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
    _extract_kb_app_ids,
    _extract_knowledge_connector_ids,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

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


def _format_browse_response(response: KnowledgeHubNodesResponse) -> tuple[bool, str]:
    """Return KnowledgeHubNodesResponse as-is (no formatting needed)."""
    if not response.success:
        return False, json.dumps({
            "status": "error",
            "message": response.error or "Failed to browse knowledge files",
        })

    # Return the full API response structure as-is
    return True, json.dumps(response.model_dump(exclude_none=True), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Agent-activity summaries for list_files — declared here (colocated with
# the tool) rather than in a central registry, per `@tool`'s
# `args_summary`/`result_summary` params (see `agent_loop_lib/tools/
# decorators.py`). Unlike most connector tools, `list_files`'s success
# envelope has no `{"message", "data": ...}` wrapper — it's `Knowledge
# HubNodesResponse.model_dump()` directly, i.e. `{"items": [...], ...}` at
# the top level — so this needs its own parsing rather than the shared
# `list_summary`/`entity_summary` factories in `app/agents/actions/util/
# tool_summaries.py`.
# ---------------------------------------------------------------------------


def _list_files_args_summary(args: dict[str, Any]) -> str | None:
    query = args.get("query")
    if isinstance(query, str) and query.strip():
        return f'Searched Knowledge Hub for "{query.strip()}"'
    return "Listed files"


def _list_files_result_summary(args: dict[str, Any], result: "ToolResult") -> str | None:
    parsed = parse_json_maybe(result.content)
    if not isinstance(parsed, dict):
        return None
    if parsed.get("status") == "error" or parsed.get("success") is False:
        return f"Listing failed: {parsed.get('message') or parsed.get('error') or 'Unknown error'}"
    items = parsed.get("items")
    if not isinstance(items, list):
        return None
    if not items:
        return "No items found"
    names = [item.get("name") for item in items if isinstance(item, dict) and item.get("name")]
    header = f"Found {len(items)} item{'s' if len(items) != 1 else ''}"
    if not names:
        return header
    return header + "\n" + bullet_list(names, total=len(items))


@ToolsetBuilder("KnowledgeHub")\
    .in_group("Internal Tools")\
    .with_description("Browse and search files in the Knowledge Hub")\
    .with_category(ToolsetCategory.UTILITY)\
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
        path="/tools/knowledgehub/list_files",
        short_description="List and search indexed items in the Knowledge Hub",
        description=(
            "List, browse, and search all items indexed in the Knowledge Hub. "
            "This includes every type of record the system indexes from connected services "
            "(use record_types and node_types parameters to filter).\n\n"
            "BROWSING: Pass parent_id + parent_type to navigate the hierarchy.\n"
            "SEARCHING: Pass query to find items by name across all sources.\n"
            "FILTERING: Use node_types and record_types to narrow results.\n\n"
            "This tool operates on indexed metadata (names, types, dates, structure). "
            "For searching WITHIN document content, use retrieval.search_internal_knowledge."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query to find files by name (2-500 chars). Leave empty to browse without searching.", required=False),
            ToolParameter(name="parent_id", type=ParameterType.STRING, description="ID of the node to browse into. Get the ID from the capability summary or from a previous list_files response. Required for browsing.", required=False),
            ToolParameter(name="parent_type", type=ParameterType.STRING, description="Type of the parent node. Required when parent_id is provided. Values: 'app' (connector root), 'recordGroup' (KB / space / drive), 'folder' (subfolder), 'record' (file with children).", required=False),
            ToolParameter(name="node_types", type=ParameterType.ARRAY, description=f"Filter results by node type. All valid values: {_NODE_TYPES_DESC}. Example: ['record'] for files only, ['folder', 'recordGroup'] for containers only.", required=False, items={"type": "string"}),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, description="Filter results to specific connectors by their IDs. Get the connector ID from the capability summary. Only needed for search (query). Not needed when browsing with parent_id.", required=False, items={"type": "string"}),
            ToolParameter(name="record_group_ids", type=ParameterType.ARRAY, description="Filter search results to specific KB collections by their record group IDs. Get IDs from the capability summary. Only applies to Collection/KB sources.", required=False, items={"type": "string"}),
            ToolParameter(name="record_types", type=ParameterType.ARRAY, description=f"Filter by record type (only applies to 'record' nodeType). All valid values: {_RECORD_TYPES_DESC}.", required=False, items={"type": "string"}),
            ToolParameter(name="only_containers", type=ParameterType.BOOLEAN, description="If true, only return containers (folders, KBs, apps) that have children.", required=False, default=False),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number for pagination (starts at 1).", required=False, default=1),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Number of items per page (1-50).", required=False, default=20),
            ToolParameter(name="sort_by", type=ParameterType.STRING, description="Sort field: 'name', 'createdAt', 'updatedAt', 'size', 'type'.", required=False, default="updatedAt"),
            ToolParameter(name="sort_order", type=ParameterType.STRING, description="Sort order: 'asc' or 'desc'.", required=False, default="desc"),
            ToolParameter(name="flattened", type=ParameterType.BOOLEAN, description="If true, return all nested items recursively instead of direct children only.", required=False, default=False),
        ],
        tags=[Tag(key="category", value="knowledge"), Tag(key="type", value="read")],
        args_summary=_list_files_args_summary,
        result_summary=_list_files_result_summary,
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
    ) -> tuple[bool, str]:
        """Browse and search files in the Knowledge Hub."""
        if not self.state:
            return False, json.dumps({
                "status": "error",
                "message": "Knowledge hub tool state not initialized",
            })

        try:
            logger_instance = self.state.get("logger", logger)
            graph_provider = self.state.get("graph_provider")
            org_id = self.state.get("org_id", "")
            user_id = self.state.get("user_id", "")

            if not graph_provider:
                return False, json.dumps({
                    "status": "error",
                    "message": "Graph provider not available",
                })

            # Extract knowledge scoping from agent configuration.
            agent_knowledge = self.state.get("agent_knowledge") or []
            agent_connector_ids = _extract_knowledge_connector_ids(agent_knowledge)
            kb_ids = set(_extract_kb_app_ids(agent_knowledge))

            if not agent_connector_ids:
                return False, json.dumps({
                    "status": "error",
                    "message": "No knowledge sources configured for this agent",
                })

            # --- Input normalization ---
            # LLMs often send empty strings instead of null for optional params
            query = query.strip() if query else None
            parent_id = parent_id.strip() if parent_id else None
            parent_type = parent_type.strip() if parent_type else None

            if parent_id and not parent_type:
                return False, json.dumps({
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

            # All connector IDs in the agent config — KB apps are now UUID-identified.
            agent_real_connector_ids = list(agent_connector_ids)

            if connector_ids:
                # LLM provided explicit connector_ids — intersect with agent config.
                # If intersection is empty (LLM passed invalid IDs), fall back to
                # full agent config so the search space isn't unnecessarily empty.
                allowed = set(agent_real_connector_ids)
                intersected = [cid for cid in connector_ids if cid in allowed]
                use_connector_ids = intersected if intersected else (agent_real_connector_ids or None)
            elif query:
                # Searching — always scope to agent's configured connectors
                use_connector_ids = agent_real_connector_ids or None
            elif not parent_id:
                # No parent, no query — root browse. Pass connector_ids to
                # scope root-level apps to only configured ones.
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
            return False, json.dumps({
                "status": "error",
                "message": f"Knowledge hub error: {str(e)}",
            })
