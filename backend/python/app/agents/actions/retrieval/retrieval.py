"""
Internal Knowledge Retrieval Tool — OPTION B IMPLEMENTATION

OPTION B: Skip get_message_content() in individual retrieval calls.
- Returns raw final_results without formatting
- Block numbering happens ONCE after all parallel calls are merged
- Prevents R-number collisions from multiple independent calls
- Cleaner architecture for multi-call agent flow
"""

import json
import logging
from typing import Any

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_flattened_results

logger = logging.getLogger(__name__)


def _normalize_list_param(value: str | list[str] | None) -> list[str] | None:
    """Normalize a parameter that should be a list of strings.
    Handles LLM sending a single string instead of a list, or empty list."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return [value] if value else None
    if isinstance(value, list):
        filtered = [str(v).strip() for v in value if v]
        return filtered if filtered else None
    return None


class RetrievalToolOutput(BaseModel):
    """Structured output from the retrieval tool."""
    status: str = Field(default="success", description="Status: 'success' or 'error'")
    content: str = Field(description="Formatted content for LLM consumption")
    final_results: list[dict[str, Any]] = Field(description="Processed results for citation generation")
    virtual_record_id_to_result: dict[str, dict[str, Any]] = Field(description="Mapping for citation normalization")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class SearchInternalKnowledgeInput(BaseModel):
    """Input schema for the search_internal_knowledge tool"""
    query: str = Field(description="The search query to find relevant information")
    limit: int | None = Field(default=50, description="Maximum number of results to return (default: 50, max: 100)")
    connector_ids: list[str] | None = Field(default=None, description="Filter to specific connectors by their IDs. If not provided or IDs don't match agent scope, uses all agent connectors.")
    collection_ids: list[str] | None = Field(default=None, description="Filter to specific KB collections by their record group IDs. If not provided or IDs don't match agent scope, uses all agent collections.")
    top_k: int | None = Field(default=None, description="Alias for limit")


@ToolsetBuilder("Retrieval")\
    .in_group("Internal Tools")\
    .with_description("Internal knowledge retrieval tool - always available, no authentication required")\
    .with_category(ToolCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/retrieval.svg"))\
    .build_decorator()
class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: ChatState | None = None, writer: StreamWriter | None = None, **kwargs) -> None:
        self.state: ChatState | None = state or kwargs.get('state')
        self.writer = writer
        logger.info("🚀 Initializing Internal Knowledge Retrieval tool")

    @tool(
        app_name="retrieval",
        tool_name="search_internal_knowledge",
        description=(
            "Search and retrieve information from internal collections and indexed applications"
        ),
        args_schema=SearchInternalKnowledgeInput,
        llm_description=(
            "Search and retrieve information from internal collections and indexed applications"
            "and connectors. Use this tool when you need to find information from "
            "company documents, knowledge bases, or connected data sources. "
            "This tool searches across all configured knowledge sources and returns "
            "relevant chunks with proper citations."
        ),
        category=ToolCategory.KNOWLEDGE,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "Questions without service mention (no Drive/Jira/Gmail/etc)",
            "Policy/procedure questions",
            "General information requests",
            "What/how/why queries (no specific app mentioned)"
        ],
        when_not_to_use=[
            "Service-specific queries (user mentions Drive, Jira, Slack, Gmail, etc.)",
            "Create/update/delete actions",
            "Real-time data requests"
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "What is our vacation policy?",
            "How do I submit expenses?",
            "Find information about Q4 results"
        ]
    )
    async def search_internal_knowledge(
        self,
        query: str | None = None,
        limit: int | None = None,
        top_k: int | None = None,
        connector_ids: list[str] | None = None,
        collection_ids: list[str] | None = None,
        **kwargs
    ) -> str:
        """Search internal knowledge bases and return formatted results."""
        search_query = query

        if not search_query:
            return json.dumps({
                "status": "error",
                "message": "No search query provided (expected 'query' or 'text' parameter)"
            })

        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Retrieval tool state not initialized"
            })

        try:
            logger_instance = self.state.get("logger", logger)
            logger_instance.info(f"🔍 Retrieval tool called with query: {search_query[:100]}")

            retrieval_service = self.state.get("retrieval_service")
            graph_provider = self.state.get("graph_provider")
            config_service = self.state.get("config_service")

            if not retrieval_service or not graph_provider:
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval services not available"
                })

            org_id = self.state.get("org_id", "")
            user_id = self.state.get("user_id", "")
            base_limit = limit or top_k or self.state.get("limit", 50)
            adjusted_limit = min(base_limit, 100)

            # Normalize list inputs
            connector_ids = _normalize_list_param(connector_ids)
            collection_ids = _normalize_list_param(collection_ids)

            # === BUILD FILTERS — always scoped to agent's configured knowledge ===
            # Get agent's configured filters from state
            agent_filters = self.state.get("filters", {}) or {}
            agent_apps = set(agent_filters.get("apps") or [])
            agent_kbs = set(agent_filters.get("kb") or [])

            # Start from agent scope (ensure it's a dict, not None)
            filter_groups = dict(agent_filters) if agent_filters else {}

            # === RESOLVE CONNECTOR IDs ===
            # Simple logic: if connector_id exists in agent scope, keep it; otherwise use all agent connectors
            if connector_ids:
                # Keep only connector IDs that exist in agent scope
                resolved_apps = [cid for cid in connector_ids if cid in agent_apps]
                # If nothing matched, fall back to full agent scope
                filter_groups["apps"] = resolved_apps if resolved_apps else (list(agent_apps) if agent_apps else [])
            else:
                # No LLM input — use full agent scope
                filter_groups["apps"] = list(agent_apps) if agent_apps else []

            # === RESOLVE COLLECTION IDs (KB record groups) ===
            # Simple logic: if collection_id exists in agent scope, keep it; otherwise use all agent collections
            if collection_ids:
                # Keep only collection IDs that exist in agent scope
                resolved_kbs = [cid for cid in collection_ids if cid in agent_kbs]
                # If nothing matched, fall back to full agent scope
                filter_groups["kb"] = resolved_kbs if resolved_kbs else (list(agent_kbs) if agent_kbs else [])
            else:
                # No LLM input — use full agent scope
                filter_groups["kb"] = list(agent_kbs) if agent_kbs else []

            # === SEARCH ===
            logger_instance.debug(f"Executing retrieval with limit: {adjusted_limit}")
            results = await retrieval_service.search_with_filters(
                queries=[search_query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=filter_groups,
                graph_provider=graph_provider,
            )

            if results is None:
                logger_instance.warning("Retrieval service returned None")
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval service returned no results"
                })

            status_code = results.get("status_code", 200)
            if status_code in [202, 500, 503]:
                return json.dumps({
                    "status": "error",
                    "status_code": status_code,
                    "message": results.get("message", "Retrieval service unavailable")
                })

            search_results = results.get("searchResults", [])
            logger_instance.info(f"✅ Retrieved {len(search_results)} documents")

            if not search_results:
                return json.dumps({
                    "status": "success",
                    "message": "No results found",
                    "results": [],
                    "result_count": 0
                })

            # === FLATTEN ===

            blob_store = BlobStorage(
                logger=logger_instance,
                config_service=config_service,
                graph_provider=graph_provider
            )

            is_multimodal_llm = False
            try:
                llm_config = self.state.get("llm")
                if hasattr(llm_config, 'model_name'):
                    model_name = str(llm_config.model_name).lower()
                    is_multimodal_llm = any(m in model_name for m in [
                        'gpt-4-vision', 'gpt-4o', 'claude-3', 'gemini-pro-vision'
                    ])
            except Exception:
                pass

            virtual_record_id_to_result = {}
            # Retrieve virtual_to_record_map from search results — same as chatbot.
            # This enriches records with graph-DB metadata (record type, web URL, etc.)
            # so that context_metadata is populated for get_message_content().
            virtual_to_record_map = results.get("virtual_to_record_map", {})

            flattened_results = await get_flattened_results(
                search_results,
                blob_store,
                org_id,
                is_multimodal_llm,
                virtual_record_id_to_result,
                virtual_to_record_map,
                graph_provider=graph_provider,
            )
            logger_instance.info(f"Processed {len(flattened_results)} flattened results")


            final_results = search_results if not flattened_results else flattened_results

            # === TRIM ===
            # Do NOT sort here. The upstream retrieval service returns results
            # ranked by relevance. merge_and_number_retrieval_results() in
            # nodes.py will correctly:
            #   1. Deduplicate blocks across parallel retrieval calls
            #   2. Group blocks by document (by best-score descending)
            #   3. Sort blocks within each document by block_index
            final_results = final_results[:adjusted_limit]

            # ================================================================
            # OPTION B: Return raw results without formatting.
            #
            # Block numbering will happen ONCE after all parallel retrieval
            # calls are merged in nodes.py (merge_and_number_retrieval_results()).
            # This prevents R-number collisions from multiple independent calls.
            #
            # The content field is just a summary for the tool result display.
            # Actual formatting happens in build_internal_context_for_response()
            # after merge and numbering.
            # ================================================================

            # Simple summary for tool result (not used for LLM context)
            agent_content = (
                f"Retrieved {len(final_results)} knowledge blocks from "
                f"{len(virtual_record_id_to_result)} documents. "
                f"Results will be formatted and numbered after merge."
            )

            logger_instance.info(
                f"✅ Retrieved {len(final_results)} raw blocks from "
                f"{len(virtual_record_id_to_result)} documents "
                f"(will be merged and numbered after all calls complete)"
            )

            output = RetrievalToolOutput(
                content=agent_content,
                final_results=final_results,
                virtual_record_id_to_result=virtual_record_id_to_result,
                metadata={
                    "query": search_query,
                    "limit": limit,
                    "result_count": len(final_results),
                    "record_count": len(virtual_record_id_to_result)
                }
            )
            return json.dumps(output.model_dump(), ensure_ascii=False)

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in retrieval tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Retrieval error: {str(e)}"
            })

