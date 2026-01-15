"""
Internal Knowledge Retrieval Tool

This tool allows the agent to search and retrieve information from internal
knowledge bases, documents, and connectors. It's an essential tool that's
always available to the agent.
"""

import json
import logging
from typing import Any, Dict, Optional

from langgraph.types import StreamWriter

from app.agents.actions.utils import run_async
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_flattened_results

logger = logging.getLogger(__name__)


class Retrieval:
    """Internal knowledge retrieval tool exposed to agents"""

    def __init__(self, state: Optional[ChatState] = None, writer: Optional[StreamWriter] = None, **kwargs) -> None:
        """Initialize the Retrieval tool

        Args:
            state: Optional chat state (can be passed via kwargs or set later)
            **kwargs: Additional arguments (may include state)
        """
        # Try to get state from kwargs or direct parameter
        self.state: Optional[ChatState] = state or kwargs.get('state')
        logger.info("🚀 Initializing Internal Knowledge Retrieval tool")

    def set_state(self, state: ChatState) -> None:
        """Set the chat state for this tool instance.

        This can be called by the tool wrapper to provide access to state.

        Args:
            state: Chat state containing services and configuration
        """
        self.state = state

    @tool(
        app_name="retrieval",
        tool_name="search_internal_knowledge",
        description=(
            "Search and retrieve information from internal knowledge bases, documents, "
            "and connectors. Use this tool when you need to find information from "
            "company documents, knowledge bases, or connected data sources. "
            "This tool searches across all configured knowledge sources and returns "
            "relevant chunks with proper citations."
        ),
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="The search query to find relevant information",
                required=True
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.NUMBER,
                description="Maximum number of results to return (default: 50, max: 100)",
                required=False
            ),
            ToolParameter(
                name="filters",
                type=ParameterType.OBJECT,
                description="Optional filters to narrow search (apps, kb, etc.)",
                required=False
            )
        ],
        category=ToolCategory.SEARCH,  # Search category for retrieval tool
        is_essential=True,
        requires_auth=False
    )
    def search_internal_knowledge(
        self,
        query: str,
        limit: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> str:
        """Search internal knowledge bases and return formatted results.

        Args:
            query: The search query
            limit: Maximum number of results (defaults to state limit or 50)
            filters: Optional filters for apps/kb

        Returns:
            Formatted string with search results ready for LLM consumption
        """
        if not self.state:
            return json.dumps({
                "status": "error",
                "message": "Retrieval tool state not initialized"
            })

        # Note: writer is not available in tool context, status updates are handled by the graph

        try:
            logger_instance = self.state.get("logger", logger)
            logger_instance.info(f"🔍 Retrieval tool called with query: {query[:100]}")

            # Get services from state
            retrieval_service = self.state.get("retrieval_service")
            arango_service = self.state.get("arango_service")
            reranker_service = self.state.get("reranker_service")
            config_service = self.state.get("config_service")

            if not retrieval_service or not arango_service:
                return json.dumps({
                    "status": "error",
                    "message": "Retrieval services not available"
                })

            # Get configuration
            org_id = self.state.get("org_id", "")
            user_id = self.state.get("user_id", "")
            base_limit = limit or self.state.get("limit", 50)
            adjusted_limit = min(base_limit, 100)  # Cap at 100

            # Use provided filters or state filters
            filter_groups = filters or self.state.get("filters", {})

            # Execute retrieval (run async operation synchronously)
            logger_instance.debug(f"Executing retrieval with limit: {adjusted_limit}")
            results = run_async(retrieval_service.search_with_filters(
                queries=[query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=filter_groups,
                arango_service=arango_service,
            ))

            # Handle errors
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

            # Extract search results
            search_results = results.get("searchResults", [])
            logger_instance.info(f"✅ Retrieved {len(search_results)} documents")

            if not search_results:
                return json.dumps({
                    "status": "success",
                    "message": "No results found",
                    "results": [],
                    "result_count": 0
                })

            # Process results like the retrieval node does
            chat_mode = self.state.get("chat_mode", "quick")

            # Initialize blob storage
            blob_store = BlobStorage(
                logger=logger_instance,
                config_service=config_service,
                arango_service=arango_service
            )

            # Determine if LLM is multimodal
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

            # Initialize virtual_record_id_to_result mapping
            virtual_record_id_to_result = {}

            # Process results (run async operations synchronously)
            flattened_results = []
            # if search_results and chat_mode != "quick":
            flattened_results = run_async(get_flattened_results(
                search_results,
                blob_store,
                org_id,
                is_multimodal_llm,
                virtual_record_id_to_result
            ))
            logger_instance.info(f"Processed {len(flattened_results)} flattened results")

            # Re-rank if needed
            should_rerank = (
                len(flattened_results) > 1
                and chat_mode != "quick"
            )

            if should_rerank and reranker_service:
                logger_instance.debug("Re-ranking results")
                final_results = run_async(reranker_service.rerank(
                    query=query,
                    documents=flattened_results,
                    top_k=adjusted_limit,
                ))
            else:
                final_results = search_results if not flattened_results else flattened_results

            # Sort results
            final_results = sorted(
                final_results,
                key=lambda x: (x.get('virtual_record_id', ''), x.get('block_index', 0))
            )

            # Limit results
            final_results = final_results[:adjusted_limit]
            # Assign block numbers for citations
            virtual_record_id_to_record_number = {}
            record_number = 1

            # First pass: map virtual_record_ids to record numbers
            for result in final_results:
                virtual_record_id = result.get("virtual_record_id")
                if not virtual_record_id:
                    metadata = result.get("metadata", {})
                    virtual_record_id = metadata.get("virtualRecordId")

                if virtual_record_id and virtual_record_id not in virtual_record_id_to_record_number:
                    virtual_record_id_to_record_number[virtual_record_id] = record_number
                    record_number += 1

            # Second pass: assign block_number to each result
            for result in final_results:
                virtual_record_id = result.get("virtual_record_id")
                if not virtual_record_id:
                    metadata = result.get("metadata", {})
                    virtual_record_id = metadata.get("virtualRecordId")

                if virtual_record_id and virtual_record_id in virtual_record_id_to_record_number:
                    assigned_record_number = virtual_record_id_to_record_number[virtual_record_id]
                    block_index = result.get("block_index", 0)
                    result["block_number"] = f"R{assigned_record_number}-{block_index}"

            logger_instance.info(f"✅ Formatted {len(final_results)} results for LLM")

            # Store results in state for citation processing (like the old node did)
            # This ensures virtual_record_id_to_result is available for citation normalization
            # CRITICAL: Store in state so it persists through the graph
            if self.state:
                self.state["final_results"] = final_results
                self.state["virtual_record_id_to_result"] = virtual_record_id_to_result

            from app.utils.chat_helpers import (
                get_message_content as get_message_content_helper,
            )
            return get_message_content_helper(final_results, virtual_record_id_to_result, "", query, logger_instance, "json")

        except Exception as e:
            logger_instance = self.state.get("logger", logger) if self.state else logger
            logger_instance.error(f"Error in retrieval tool: {str(e)}", exc_info=True)
            return json.dumps({
                "status": "error",
                "message": f"Retrieval error: {str(e)}"
            })


