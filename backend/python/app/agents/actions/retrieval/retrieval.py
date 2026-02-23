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
from typing import Any, Dict, List, Optional

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


class RetrievalToolOutput(BaseModel):
    """Structured output from the retrieval tool."""
    status: str = Field(default="success", description="Status: 'success' or 'error'")
    content: str = Field(description="Formatted content for LLM consumption")
    final_results: List[Dict[str, Any]] = Field(description="Processed results for citation generation")
    virtual_record_id_to_result: Dict[str, Dict[str, Any]] = Field(description="Mapping for citation normalization")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class SearchInternalKnowledgeInput(BaseModel):
    """Input schema for the search_internal_knowledge tool"""
    query: str = Field(description="The search query to find relevant information")
    limit: Optional[int] = Field(default=50, description="Maximum number of results to return (default: 50, max: 100)")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Optional filters to narrow search (apps, kb, etc.)")
    text: Optional[str] = Field(default=None, description="Alternative parameter name for query")
    top_k: Optional[int] = Field(default=None, description="Alias for limit")


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

    def __init__(self, state: Optional[ChatState] = None, writer: Optional[StreamWriter] = None, **kwargs) -> None:
        self.state: Optional[ChatState] = state or kwargs.get('state')
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
        query: Optional[str] = None,
        text: Optional[str] = None,
        limit: Optional[int] = None,
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """Search internal knowledge bases and return formatted results."""
        search_query = query or text

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
            reranker_service = self.state.get("reranker_service")
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
            filter_groups = filters or self.state.get("filters", {})

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

            # === FALLBACK: if app filter returned 0 accessible records, retry without app filter ===
            # This handles the case where the LLM passes filters.apps for an app that is only
            # available as a live API toolset (not indexed as a connector). In that case, we
            # fall back to searching all available indexed sources (e.g. KB).
            if not search_results and filter_groups and filter_groups.get("apps"):
                logger_instance.info(
                    f"No results with app filter {filter_groups.get('apps')} — "
                    "retrying without app filter (app may not be indexed as a connector)"
                )
                fallback_filters = {k: v for k, v in filter_groups.items() if k != "apps"}
                fallback_results = await retrieval_service.search_with_filters(
                    queries=[search_query],
                    org_id=org_id,
                    user_id=user_id,
                    limit=adjusted_limit,
                    filter_groups=fallback_filters,
                    graph_provider=graph_provider,
                )
                if fallback_results and fallback_results.get("status_code", 200) not in [202, 500, 503]:
                    fallback_search = fallback_results.get("searchResults", [])
                    if fallback_search:
                        logger_instance.info(
                            f"Fallback retrieval (no app filter) returned {len(fallback_search)} results"
                        )
                        results = fallback_results
                        search_results = fallback_search
            logger_instance.info(f"✅ Retrieved {len(search_results)} documents")

            if not search_results:
                return json.dumps({
                    "status": "success",
                    "message": "No results found",
                    "results": [],
                    "result_count": 0
                })

            # === FLATTEN ===
            # Default to "standard" (not "quick") so reranking is enabled by default,
            # matching the chatbot's behaviour.
            chat_mode = self.state.get("chat_mode", "standard")

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

            # === RERANK ===
            # Match chatbot behaviour: rerank unless explicitly in quick mode.
            # Default was "quick" which silently disabled reranking — fixed here.
            should_rerank = (
                len(flattened_results) > 1
                and chat_mode not in ("quick",)
            )

            if should_rerank and reranker_service:
                logger_instance.debug("Re-ranking results")
                final_results = await reranker_service.rerank(
                    query=search_query,
                    documents=flattened_results,
                    top_k=adjusted_limit,
                )
            else:
                final_results = search_results if not flattened_results else flattened_results

            # === TRIM ===
            # Do NOT sort here. The reranker has already ordered results by relevance.
            # merge_and_number_retrieval_results() in nodes.py will correctly:
            #   1. Deduplicate blocks across parallel retrieval calls
            #   2. Group blocks by document (by best-score descending)
            #   3. Sort blocks within each document by block_index
            # Any intermediate sort here would discard the reranker's ordering and
            # produce incorrect document-to-R-label assignments.
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

