"""
Internal Knowledge Retrieval Tool

This tool allows the agent to search and retrieve information from internal
knowledge bases, documents, and connectors. It's an essential tool that's
always available to the agent.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agents.actions.utils import run_async
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_flattened_results

logger = logging.getLogger(__name__)


class RetrievalToolOutput(BaseModel):
    """Structured output from the retrieval tool.

    This ensures proper data flow between tool and graph without hacky state mutations.
    """
    content: str = Field(description="Formatted content for LLM consumption")
    final_results: List[Dict[str, Any]] = Field(description="Processed results for citation generation")
    virtual_record_id_to_result: Dict[str, Dict[str, Any]] = Field(description="Mapping for citation normalization")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

class SearchInternalKnowledgeInput(BaseModel):
    """Input schema for the search_internal_knowledge tool"""
    query: str = Field(description="The search query to find relevant information")
    limit: Optional[int] = Field(default=50, description="Maximum number of results to return (default: 50, max: 100)")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Optional filters to narrow search (apps, kb, etc.)")
    text: Optional[str] = Field(default=None, description="Alternative parameter name for query (LLM sometimes uses 'text' instead)")
    top_k: Optional[int] = Field(default=None, description="Alias for limit (LLM sometimes uses 'top_k' instead)")
# Register Retrieval toolset (internal - always available, no auth required, backend-only)
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
        """Initialize the Retrieval tool

        Args:
            state: Chat state passed by graph
            writer: Stream writer for real-time updates
            **kwargs: Additional arguments
        """
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
        category=ToolCategory.SEARCH,  # Search category for retrieval tool
        is_essential=True,
        requires_auth=False
    )
    def search_internal_knowledge(
        self,
        query: Optional[str] = None,
        text: Optional[str] = None,  # Accept 'text' as alias for 'query' (LLM sometimes uses this)
        limit: Optional[int] = None,
        top_k: Optional[int] = None,  # Accept 'top_k' as alias for 'limit' (LLM sometimes uses this)
        filters: Optional[Dict[str, Any]] = None,
        **kwargs  # Accept any other args LLM might pass (e.g., context, sources)
    ) -> str:
        """Search internal knowledge bases and return formatted results.

        Args:
            query: The search query
            text: Alternative parameter name for query (LLM sometimes uses 'text' instead)
            limit: Maximum number of results (defaults to state limit or 50)
            top_k: Alias for limit (LLM sometimes uses 'top_k' instead)
            filters: Optional filters for apps/kb

        Returns:
            Formatted string with search results ready for LLM consumption
        """
        # Accept both 'query' and 'text' parameters (LLM sometimes uses different names)
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

        # Note: writer is not available in tool context, status updates are handled by the graph

        try:
            logger_instance = self.state.get("logger", logger)
            logger_instance.info(f"🔍 Retrieval tool called with query: {search_query[:100]}")

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
            # Accept both 'limit' and 'top_k' (LLM uses different names)
            base_limit = limit or top_k or self.state.get("limit", 50)
            adjusted_limit = min(base_limit, 100)  # Cap at 100

            # Use provided filters or state filters
            filter_groups = filters or self.state.get("filters", {})

            # Execute retrieval (run async operation synchronously)
            logger_instance.debug(f"Executing retrieval with limit: {adjusted_limit}")
            results = run_async(retrieval_service.search_with_filters(
                queries=[search_query],
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
                    query=search_query,
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

            # ⚡ CRITICAL: Format blocks EXACTLY like the chatbot does (using same structure)
            # This ensures the agent sees the SAME context format as chatbot and can cite properly
            # Use the chat_helpers formatting approach for consistency


            from app.utils.chat_helpers import get_message_content

            # Build message content exactly like chatbot does (see chatbot.py line 356, 637)
            # This includes semantic metadata, proper block numbers, and citation instructions
            try:
                # Format using chatbot's approach for consistency
                message_content = get_message_content(
                    final_results,
                    virtual_record_id_to_result,
                    "",  # user_data (not needed for agent)
                    search_query,  # query
                    logger_instance,
                    mode="json"  # Use JSON mode for proper formatting
                )

                # Convert content structure to plain text for agent tool result
                # message_content is a list of dicts like [{"type": "text", "text": "..."}]
                formatted_parts = []
                for item in message_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        formatted_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        formatted_parts.append(item)

                agent_content = "\n".join(formatted_parts)

                # Add CRITICAL instructions at the top to prevent duplicate retrieval
                header = []
                header.append("=" * 80)
                header.append("📚 INTERNAL KNOWLEDGE RETRIEVED - USE THIS TO ANSWER")
                header.append("=" * 80)
                header.append("")
                header.append(f"✅ SUCCESS! Retrieved {len(final_results)} relevant blocks from {len(virtual_record_id_to_result)} documents.")
                header.append("")
                header.append("⚠️ **CRITICAL INSTRUCTIONS:**")
                header.append("1. **ANSWER IMMEDIATELY** using the blocks below (DO NOT call retrieval again!)")
                header.append("2. **CITE EVERY FACT** with [R1-1] style citations (see block numbers below)")
                header.append("3. **BE COMPREHENSIVE** - provide detailed answers, not summaries")
                header.append("4. **USE JSON FORMAT** with 'answer', 'reason', 'confidence', 'answerMatchType', 'blockNumbers'")
                header.append("")
                header.append("🚫 **DO NOT**:")
                header.append("   - Call retrieval_search_internal_knowledge again (you already have the data!)")
                header.append("   - Say 'I searched' or 'The tool returned' (just give the answer)")
                header.append("   - Make up facts (use ONLY the data below)")
                header.append("")
                header.append("=" * 80)
                header.append("")

                agent_content = "\n".join(header) + "\n" + agent_content

                logger_instance.info(f"✅ Formatted as chatbot-style content: {len(agent_content)} chars, {len(final_results)} blocks from {len(virtual_record_id_to_result)} documents")

            except Exception as format_error:
                logger_instance.error(f"Failed to format with chat_helpers, falling back: {format_error}")
                # Fallback to simple formatting if chat_helpers fails
                agent_content = f"Retrieved {len(final_results)} blocks. Answer the question using these blocks with [R citations].\n\n"
                for i, result in enumerate(final_results[:20]):
                    block_num = result.get("block_number", f"R?-{i}")
                    content = str(result.get("content", ""))[:300]
                    agent_content += f"Block {block_num}: {content}\n\n"

            # Return structured output with chatbot-formatted blocks
            output = RetrievalToolOutput(
                content=agent_content,  # Chatbot-style formatted blocks with citations
                final_results=final_results,  # All results for final response
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


