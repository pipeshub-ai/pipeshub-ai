"""
Internal Knowledge Retrieval Tool — FIXED

Fix: Uses get_message_content() from chat_helpers.py (the SAME function the chatbot uses)
to format blocks with R-markers. Then syncs block_number on each result dict so that
downstream code (build_internal_context_for_response, process_citations) sees matching numbers.

Previously broken because:
1. retrieval.py assigned block_number BEFORE calling get_message_content()
2. get_message_content() assigned its OWN record_number internally  
3. response_prompt.py recomputed block_number a THIRD time
→ Three conflicting numbering schemes → LLM confused → UUID citations

Now: get_message_content() is the SINGLE source of truth (matches chatbot exactly).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from langgraph.types import StreamWriter
from pydantic import BaseModel, Field

from app.agents.actions.utils import run_async
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_flattened_results, get_message_content

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
    def search_internal_knowledge(
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
            arango_service = self.state.get("arango_service")
            reranker_service = self.state.get("reranker_service")
            config_service = self.state.get("config_service")

            if not retrieval_service or not arango_service:
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
            results = run_async(retrieval_service.search_with_filters(
                queries=[search_query],
                org_id=org_id,
                user_id=user_id,
                limit=adjusted_limit,
                filter_groups=filter_groups,
                arango_service=arango_service,
            ))

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
            chat_mode = self.state.get("chat_mode", "quick")

            blob_store = BlobStorage(
                logger=logger_instance,
                config_service=config_service,
                arango_service=arango_service
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

            flattened_results = run_async(get_flattened_results(
                search_results,
                blob_store,
                org_id,
                is_multimodal_llm,
                virtual_record_id_to_result
            ))
            logger_instance.info(f"Processed {len(flattened_results)} flattened results")

            # === RERANK ===
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

            # === SORT (same as chatbot) ===
            final_results = sorted(
                final_results,
                key=lambda x: (x.get('virtual_record_id', ''), x.get('block_index', 0))
            )
            final_results = final_results[:adjusted_limit]

            # ================================================================
            # FIX: Do NOT assign block_number here before get_message_content().
            #
            # OLD BROKEN CODE (REMOVE THIS):
            #   virtual_record_id_to_record_number = {}
            #   record_number = 1
            #   for result in final_results:
            #       ...
            #       result["block_number"] = f"R{assigned_record_number}-{block_index}"
            #
            # This was NUMBERING #1 which conflicted with get_message_content()'s
            # internal NUMBERING #2.
            # ================================================================

            # ================================================================
            # FIX: Use get_message_content() — the EXACT same function the
            # chatbot uses (chatbot.py line ~320, streaming endpoint line ~380).
            #
            # This function:
            #   1. Renders qna_prompt_instructions_1 (task/tools/context header)
            #   2. For each result: assigns block_number = f"R{record_number}-{block_index}"
            #      and formats as "* Block Number: R1-0\n* Block Type: text\n* Block Content: ..."
            #   3. Renders qna_prompt_instructions_2 (instructions/output format/examples)
            #
            # The prompt templates (qna_prompt_instructions_1, _2) already contain
            # perfect citation instructions with examples like [R1-2][R2-5].
            # ================================================================
            user_data = self.state.get("user_data", "")

            message_content = get_message_content(
                final_results,
                virtual_record_id_to_result,
                user_data,
                search_query,
                logger_instance,
                mode="json"
            )

            # get_message_content() returns a list of content dicts:
            #   [{"type": "text", "text": "..."}, {"type": "text", "text": "..."}, ...]
            # Extract the text parts into a single string for the tool result.
            formatted_parts = []
            for item in message_content:
                if isinstance(item, dict) and item.get("type") == "text":
                    formatted_parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    formatted_parts.append(item)

            agent_content = "\n".join(formatted_parts)

            # ================================================================
            # FIX: Sync block_number on each result dict so it matches what
            # get_message_content() put in the formatted text.
            #
            # This is needed because:
            # - process_citations() uses result["block_number"]
            # - build_internal_context_for_response() uses result["block_number"]
            # - Both need to see the SAME R-markers as the formatted text
            #
            # The logic below replicates get_message_content()'s record_number
            # tracking exactly (from chat_helpers.py):
            #   record_number = 1
            #   for i, result in enumerate(flattened_results):
            #       if virtual_record_id not in seen:
            #           if i > 0: record_number += 1
            #           seen.add(virtual_record_id)
            #       block_number = f"R{record_number}-{block_index}"
            # ================================================================
            _sync_block_numbers_from_chatbot_format(final_results)

            logger_instance.info(
                f"✅ Formatted retrieval content (chatbot-style): {len(agent_content)} chars, "
                f"{len(final_results)} blocks from {len(virtual_record_id_to_result)} documents"
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


def _sync_block_numbers_from_chatbot_format(final_results: List[Dict[str, Any]]) -> None:
    """
    Assign block_number on each result using the EXACT same logic as
    get_message_content() in chat_helpers.py.

    This ensures result["block_number"] matches the R-markers that
    get_message_content() put in the formatted text.

    Logic (from chat_helpers.py get_message_content()):
    
        seen_virtual_record_ids = set()
        seen_blocks = set()
        record_number = 1
        for i, result in enumerate(flattened_results):
            virtual_record_id = result.get("virtual_record_id")
            if virtual_record_id not in seen_virtual_record_ids:
                if i > 0:
                    # close previous record tag
                    record_number = record_number + 1
                seen_virtual_record_ids.add(virtual_record_id)
                ...
            # For each unique block:
            block_number = f"R{record_number}-{block_index}"
    
    So: first record = R1, second = R2, third = R3, etc.
    record_number starts at 1, increments when a new virtual_record_id 
    is encountered (but NOT for the very first one, only when i > 0).
    """
    seen_virtual_record_ids = set()
    record_number = 1

    for i, result in enumerate(final_results):
        virtual_record_id = result.get("virtual_record_id")
        if not virtual_record_id:
            virtual_record_id = result.get("metadata", {}).get("virtualRecordId")

        if virtual_record_id and virtual_record_id not in seen_virtual_record_ids:
            if i > 0:
                record_number += 1
            seen_virtual_record_ids.add(virtual_record_id)

        block_index = result.get("block_index", 0)
        result["block_number"] = f"R{record_number}-{block_index}"