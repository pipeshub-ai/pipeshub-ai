"""
Knowledge Hub Internal Tool

Allows agents to browse and search files, folders, and knowledge bases
in the Knowledge Hub, automatically scoped to the agent's configured
knowledge sources. Complements the retrieval tool: this browses file
metadata/structure, while retrieval searches file contents.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.modules.agents.qna.chat_state import (
    ChatState,
    _extract_kb_record_groups,
    _extract_knowledge_connector_ids,
)

logger = logging.getLogger(__name__)

# Valid values for input validation
_VALID_NODE_TYPES = {"kb", "app", "folder", "recordGroup", "record"}
_VALID_SORT_FIELDS = {"name", "createdAt", "updatedAt", "size", "type"}
_VALID_SORT_ORDERS = {"asc", "desc"}


class ListFilesInput(BaseModel):
    """Input schema for the list_files tool"""
    query: Optional[str] = Field(
        default=None,
        description=(
            "Search query to find files by name (2-500 chars). "
            "Leave empty to browse without searching."
        ),
    )
    parent_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of a folder, knowledge base, app, or record group to browse into. "
            "Leave empty to browse root level."
        ),
    )
    parent_type: Optional[str] = Field(
        default=None,
        description=(
            "Type of the parent node: 'kb', 'app', 'folder', 'recordGroup'. "
            "Required when parent_id is provided."
        ),
    )
    node_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "Filter by node types. Options: 'kb', 'app', 'folder', 'recordGroup', 'record'. "
            "Example: ['record'] for files only."
        ),
    )
    record_types: Optional[List[str]] = Field(
        default=None,
        description="Filter by record types (e.g., specific file types).",
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


def _normalize_list_param(value: Any) -> Optional[List[str]]:
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
        node: Dict[str, Any] = {
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

    result: Dict[str, Any] = {
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

    def __init__(self, state: Optional[ChatState] = None, **kwargs) -> None:
        self.state: Optional[ChatState] = state or kwargs.get('state')

    @tool(
        app_name="knowledge_hub",
        tool_name="list_files",
        description="List and search files, folders, and knowledge bases in the internal knowledge hub",
        args_schema=ListFilesInput,
        llm_description=(
            "List and search files, folders, and knowledge bases in the internal knowledge hub. "
            "Use this to list files in a folder or knowledge base, find files by name, "
            "or explore the knowledge structure. Supports pagination for large result sets. "
            "Results are automatically filtered to only show knowledge sources configured for this agent.\n\n"
            "Use knowledge_hub.list_files when: listing files, browsing folder structures, "
            "exploring what files/documents exist, finding files by name or metadata, paginating results.\n"
            "Use retrieval.search_internal_knowledge when: searching for information WITHIN documents, "
            "answering questions from document content, finding relevant knowledge chunks."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User wants to list or browse files in a knowledge base or folder",
            "User asks what files or documents are available",
            "User wants to find a specific file by name",
            "User wants to explore the structure of knowledge sources",
            "User needs to know the hierarchy of folders and files",
        ],
        when_not_to_use=[
            "User wants to search file CONTENTS (use retrieval.search_internal_knowledge instead)",
            "User wants to create, update, or delete files",
            "User asks about file content rather than file metadata",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What files are in the knowledge base?",
            "List all documents in the HR folder",
            "Find files named 'policy'",
            "Show me the folder structure",
            "What knowledge sources are available?",
        ],
    )
    async def list_files(
        self,
        query: Optional[str] = None,
        parent_id: Optional[str] = None,
        parent_type: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        record_types: Optional[List[str]] = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 20,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
        flattened: bool = False,
        **kwargs,
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

            # Extract knowledge scoping from agent configuration
            agent_knowledge = self.state.get("agent_knowledge") or []
            connector_ids = _extract_knowledge_connector_ids(agent_knowledge)
            kb_ids = _extract_kb_record_groups(agent_knowledge)

            if not connector_ids and not kb_ids:
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
            if query and len(query) < 2:
                query = None
            elif query and len(query) > 500:
                query = query[:500]

            # Normalize list params (handle LLM sending string instead of list,
            # or empty list instead of null)
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
                f"page={page}, limit={limit}"
            )

            service = KnowledgeHubService(
                logger=logger_instance,
                graph_provider=graph_provider,
            )

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
                connector_ids=connector_ids,
                kb_ids=kb_ids,
                flattened=flattened,
            )

            return _format_browse_response(response)

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in knowledge hub tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Knowledge hub error: {str(e)}",
            })
