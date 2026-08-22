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

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.tool_summaries import bullet_list, parse_json_maybe
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.agents.actions.knowledge_graph.ops.scope import resolve_scope
from app.modules.agents.qna.chat_state import (
    ChatState,
    remember_record_ids,
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

# Query validation constants
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 500



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


_FETCHABLE_NODE_TYPES = frozenset({NodeType.RECORD.value, NodeType.FOLDER.value})


def _record_ids_in_items(items: list[NodeItem] | None) -> list[str]:
    """Record IDs from a listing that `knowledgegraph__fetch_record` can read —
    app and recordGroup ids cannot be fetched."""
    ids: list[str] = []
    for item in items or []:
        node_type = getattr(item.nodeType, "value", item.nodeType)
        if node_type in _FETCHABLE_NODE_TYPES and item.id:
            ids.append(item.id)
    return ids


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
    .as_essential()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/knowledge_hub.svg"))\
    .build_decorator()
class KnowledgeHub:
    """Knowledge Hub tool for browsing files and folders in the Knowledge Hub"""

    def __init__(self, state: ChatState | None = None) -> None:
        self.state: ChatState | None = state

    @tool(
        path="/tools/knowledgehub/list_files",
        short_description="Search indexed items in the Knowledge Hub by name",
        description=(
            "Search for items indexed in the Knowledge Hub by name across all sources. "
            "Use this to find records when you know part of the name.\n\n"
            "Results are records, not content: each item's id is a Record ID — pass it to "
            "knowledgegraph.navigate(node_id=...) to see what is under it, or to "
            "knowledgegraph__fetch_record to read it.\n\n"
            "For BROWSING the hierarchy (App → RecordGroup → Record → children), use "
            "knowledgegraph__navigate instead — it takes a single node_id "
            "and handles all node types without requiring parent_type.\n\n"
            "For searching WITHIN document content, use knowledgegraph__search.\n\n"
            "FILTERING: Use node_types and record_types to narrow results by type."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Search query to find files by name (2-500 chars). Required.", required=True),
            ToolParameter(name="node_types", type=ParameterType.ARRAY, description=f"Filter results by node type. All valid values: {_NODE_TYPES_DESC}. Example: ['record'] for files only.", required=False, items={"type": "string"}),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, description="Filter results to specific connectors by their IDs. Get the connector ID from the capability summary.", required=False, items={"type": "string"}),
            ToolParameter(name="record_group_ids", type=ParameterType.ARRAY, description="Filter search results to specific KB collections by their record group IDs. Only applies to Collection/KB sources.", required=False, items={"type": "string"}),
            ToolParameter(name="record_types", type=ParameterType.ARRAY, description="Filter by record type (only applies to 'record' nodeType). E.g. 'CONFLUENCE_PAGE', 'FILE', 'TICKET'.", required=False, items={"type": "string"}),
            ToolParameter(name="page", type=ParameterType.INTEGER, description="Page number for pagination (starts at 1).", required=False, default=1),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Number of items per page (1-50).", required=False, default=20),
            ToolParameter(name="sort_by", type=ParameterType.STRING, description="Sort field: 'name', 'createdAt', 'updatedAt', 'size', 'type'.", required=False, default="updatedAt"),
            ToolParameter(name="sort_order", type=ParameterType.STRING, description="Sort order: 'asc' or 'desc'.", required=False, default="desc"),
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

            scope = await resolve_scope(self.state, allow_catalog_fallback=True)
            if scope.is_empty():
                return False, json.dumps({
                    "status": "error",
                    "message": "No knowledge sources configured for this agent",
                })
            agent_connector_ids = list(scope.app_ids)
            kb_ids = set(scope.kb_ids)

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

            remember_record_ids(self.state, _record_ids_in_items(response.items))
            return _format_browse_response(response)

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in knowledge hub tool: {str(e)}", exc_info=True)
            return False, json.dumps({
                "status": "error",
                "message": f"Knowledge hub error: {str(e)}",
            })
