"""
Retrieval Module

Handles knowledge base retrieval for the QNA agent.
Determines when retrieval is needed and executes vector search
across configured knowledge bases and connectors.
"""

import logging
import uuid
from datetime import datetime

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import StreamWriter

from app.models.blocks import BlockType, GroupType
from app.modules.agents.qna.chat_state import ChatState
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_flattened_results

logger = logging.getLogger(__name__)


async def conditional_retrieve_node(state: ChatState, config: RunnableConfig, writer: StreamWriter) -> ChatState:
    """
    Smart retrieval based on query analysis.

    Only performs retrieval when the query needs internal knowledge.
    For follow-up queries with existing context, skips retrieval.

    Args:
        state: Current chat state with query analysis
        config: Runnable config for context preservation
        writer: StreamWriter for status updates

    Returns:
        Updated chat state with search results
    """
    try:
        logger_instance = state["logger"]

        # Check for errors from previous nodes
        if state.get("error"):
            return state

        # Get query analysis
        analysis = state.get("query_analysis", {})

        # Skip retrieval if not needed
        if not analysis.get("needs_internal_data", False):
            logger_instance.info("Skipping retrieval - using conversation context")
            state["search_results"] = []
            state["final_results"] = []
            return state

        # Perform retrieval
        logger_instance.info("📚 Gathering knowledge sources...")
        try:
            if writer:
                # StreamWriter accepts ONE argument - a dict with event and data
                writer({"event": "status", "data": {"status": "retrieving", "message": "📚 Gathering knowledge sources..."}})
        except Exception as e:
            logger_instance.debug(f"Failed to write stream event: {e}")

        # Get services
        retrieval_service = state["retrieval_service"]
        arango_service = state["arango_service"]

        # Get configuration
        query = state["query"]
        org_id = state["org_id"]
        user_id = state["user_id"]
        limit = state.get("limit", 50)
        filter_groups = state.get("filters", {})

        # Adjust limit based on complexity
        analysis = state.get("query_analysis", {})
        is_complex = analysis.get("is_complex", False)
        adjusted_limit = min(limit * 2, 100) if is_complex else limit

        logger_instance.debug(f"Using retrieval limit: {adjusted_limit} (complex: {is_complex})")

        # Execute retrieval using correct API
        results = await retrieval_service.search_with_filters(
            queries=[query],  # Expects list of queries
            org_id=org_id,
            user_id=user_id,
            limit=adjusted_limit,
            filter_groups=filter_groups,
            arango_service=arango_service,
            is_agent=True
        )

        # Handle case where retrieval service returns None
        if results is None:
            logger_instance.warning("Retrieval service returned None, treating as empty results")
            state["search_results"] = []
            state["final_results"] = []
            return state

        # Check for error status codes
        status_code = results.get("status_code", 200)
        if status_code in [202, 500, 503]:
            state["error"] = {
                "status_code": status_code,
                "status": results.get("status", "error"),
                "message": results.get("message", "Retrieval service unavailable"),
            }
            return state

        # Extract search results from response
        search_results = results.get("searchResults", [])
        logger_instance.info(f"Retrieved {len(search_results)} documents")

        if not search_results:
            state["search_results"] = []
            state["final_results"] = []
            state["virtual_record_id_to_result"] = {}
            return state

        # Process search results like chatbot does - CRITICAL for proper formatting and citations
        try:
            if writer:
                # StreamWriter accepts ONE argument - a dict with event and data
                writer({"event": "status", "data": {"status": "processing", "message": "Processing search results..."}})
        except Exception as e:
            logger_instance.debug(f"Failed to write stream event: {e}")

        # Initialize blob storage for processing results
        config_service = state["config_service"]
        arango_service = state["arango_service"]
        blob_store = BlobStorage(
            logger=logger_instance,
            config_service=config_service,
            arango_service=arango_service
        )
        state["blob_store"] = blob_store

        # Determine if LLM is multimodal (needed for get_flattened_results)
        # Check LLM config to determine if multimodal
        is_multimodal_llm = False
        try:
            llm_config = state.get("llm")
            if hasattr(llm_config, 'model_name'):
                model_name = str(llm_config.model_name).lower()
                # Common multimodal models
                is_multimodal_llm = any(m in model_name for m in ['gpt-4-vision', 'gpt-4o', 'claude-3', 'gemini-pro-vision'])
        except Exception:
            pass
        state["is_multimodal_llm"] = is_multimodal_llm

        # Initialize virtual_record_id_to_result mapping (CRITICAL for citations)
        virtual_record_id_to_result = {}
        state["virtual_record_id_to_result"] = virtual_record_id_to_result

        # Process results through get_flattened_results (like chatbot)
        # This properly formats results with metadata, block_index, virtual_record_id, etc.
        logger_instance.info(f"Processing {len(search_results)} search results")
        chat_mode = state.get("chat_mode", "quick")
        logger_instance.info(f"Chat mode: {chat_mode}")
        flattened_results = await get_flattened_results(
            search_results,
            blob_store,
            org_id,
            is_multimodal_llm,
            virtual_record_id_to_result
        )

        logger_instance.info(f"Processed {len(flattened_results)} flattened results")

        # Re-rank results only when chatMode is "standard" (like chatbot)
        # Reranking happens when: chatMode != "quick"
        reranker_service = state["reranker_service"]


        should_rerank = (
            len(flattened_results) > 1
            and chat_mode != "quick"
        )

        if should_rerank:
            try:
                if writer:
                    # StreamWriter accepts ONE argument - a dict with event and data
                    writer({"event": "status", "data": {"status": "ranking", "message": "Ranking relevant information..."}})
            except Exception as e:
                logger_instance.debug(f"Failed to write stream event: {e}")

            final_results = await reranker_service.rerank(
                query=query,
                documents=flattened_results,
                top_k=adjusted_limit,
            )
        else:
            final_results = search_results

        # Sort results by virtual_record_id and block_index (like chatbot)
        final_results = sorted(
            final_results,
            key=lambda x: (x.get('virtual_record_id', ''), x.get('block_index', 0))
        )

        # Limit to adjusted_limit
        final_results = final_results[:adjusted_limit]

        # Set block_number in final_results for citation processing
        # Group by virtual_record_id to assign record numbers consistently
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

        # Store processed results
        state["search_results"] = search_results  # Keep original for reference
        state["final_results"] = final_results  # Processed and formatted results
        state["virtual_record_id_to_result"] = virtual_record_id_to_result  # CRITICAL for citations

        logger_instance.info(f"Final processed results: {len(final_results)} documents with proper formatting")

        # Inject knowledge as a tool result for the LLM to use
        if final_results:
            _inject_knowledge_as_tool_result(state, final_results, virtual_record_id_to_result, logger_instance)

        # Clean up retrieval artifacts to reduce state pollution
        from app.modules.agents.qna.chat_state import cleanup_state_after_retrieval
        cleanup_state_after_retrieval(state)
        logger_instance.debug("Cleaned up retrieval artifacts")

        return state

    except Exception as e:
        logger.error(f"Error in retrieval: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 500, "detail": f"Retrieval error: {str(e)}"}
        return state


def _inject_knowledge_as_tool_result(
    state: ChatState,
    final_results: list,
    virtual_record_id_to_result: dict,
    logger_instance
) -> None:
    """
    Inject knowledge retrieval results as a tool result so the LLM can use it.
    This makes the knowledge available as a tool execution result rather than in the system prompt.
    """
    try:
        # Format knowledge as a structured tool result (matching chatbot's approach)
        knowledge_content_parts = [
            "## 📚 INTERNAL KNOWLEDGE RETRIEVED - MANDATORY CITATION RULES",
            "",
            "=" * 80,
            "⚠️⚠️⚠️ CRITICAL: YOU MUST INCLUDE INLINE CITATIONS [R1-1] IN YOUR ANSWER ⚠️⚠️⚠️",
            "=" * 80,
            "",
            "**CITATION FORMAT EXAMPLE (FOLLOW THIS EXACTLY):**",
            "",
            "```markdown",
            "Asana announced Q4 FY2024 results on March 11, 2024 [R1-1]. The company",
            "achieved $142 million improvement in cash flows [R1-1]. Annual revenues from",
            "customers spending $100,000+ grew 29% year over year [R1-2].",
            "```",
            "",
            "**NOTICE**: Each fact has [R1-1] or [R1-2] IMMEDIATELY after it!",
            "",
            "=" * 80,
            "📋 YOUR MANDATORY INSTRUCTIONS:",
            "=" * 80,
            "",
            "1. **INCLUDE [R CITATIONS IN YOUR ANSWER** (NOT OPTIONAL!)",
            "   - Each block below has a \"Block Number\" like R1-1, R1-2, R2-3",
            "   - Put [R1-1] IMMEDIATELY after EACH fact: \"Revenue grew 29% [R1-1].\"",
            "   - Format: [R1-1][R2-3] NOT [R1-1, R2-3]",
            "   - If you don't include citations, the answer will be REJECTED",
            "",
            "2. **ANSWER DIRECTLY (No Meta-Commentary)**",
            "   - ❌ DON'T say: \"I searched and found...\"",
            "   - ❌ DON'T say: \"The tool returned...\"",
            "   - ❌ DON'T say: \"Based on the retrieved information...\"",
            "   - ✅ DO say: \"Asana announced results on March 11, 2024 [R1-1].\"",
            "",
            "3. **BE COMPREHENSIVE**",
            "   - Provide detailed, thorough answers (not brief summaries)",
            "   - Include ALL relevant information from the blocks",
            "   - Use markdown formatting (headers, lists, tables, bold)",
            "",
            "4. **JSON OUTPUT FORMAT**",
            "   ```json",
            "   {",
            '     "answer": "Detailed answer with [R1-1] citations [R2-3] inline.",',
            '     "reason": "Brief explanation",',
            '     "confidence": "Very High | High | Medium | Low",',
            '     "answerMatchType": "Derived From Chunks",',
            '     "blockNumbers": ["R1-1", "R1-2", "R2-3"]',
            "   }",
            "   ```",
            "",
            "=" * 80,
            "⚠️ REMINDER: Your answer MUST contain [R1-1] style citations!",
            "=" * 80,
            "",
            "## 📄 Retrieved Knowledge Blocks (Use These Block Numbers in Citations)",
            ""
        ]

        # Group results by virtual_record_id and format like chatbot
        seen_virtual_record_ids = set()
        seen_blocks = set()
        record_number = 1

        for result in final_results:
            virtual_record_id = result.get("virtual_record_id")
            if not virtual_record_id:
                metadata = result.get("metadata", {})
                virtual_record_id = metadata.get("virtualRecordId")

            if not virtual_record_id:
                continue

            # Start new record if we haven't seen this virtual_record_id
            if virtual_record_id not in seen_virtual_record_ids:
                if record_number > 1:
                    knowledge_content_parts.append("</record>")

                seen_virtual_record_ids.add(virtual_record_id)

                # Get record info from virtual_record_id_to_result if available
                record = None
                if virtual_record_id_to_result and virtual_record_id in virtual_record_id_to_result:
                    record = virtual_record_id_to_result[virtual_record_id]

                # Format record header
                metadata = result.get("metadata", {})
                record_id = record.get("id", "Not available") if record else metadata.get("recordId", "Not available")
                record_name = record.get("record_name", "Not available") if record else metadata.get("recordName", metadata.get("origin", "Unknown"))

                knowledge_content_parts.append("<record>")
                knowledge_content_parts.append(f"* Record Id: {record_id}")
                knowledge_content_parts.append(f"* Record Name: {record_name}")

                # Add semantic metadata if available (CRITICAL for better context!)
                if record:
                    # Extract semantic metadata like chatbot does
                    semantic_metadata = record.get("semantic_metadata", {})
                    if isinstance(semantic_metadata, dict) and semantic_metadata:
                        knowledge_content_parts.append("* Record Summary with metadata:")
                        if semantic_metadata.get("summary"):
                            knowledge_content_parts.append(f"  - Summary: {semantic_metadata.get('summary')}")
                        if semantic_metadata.get("categories"):
                            knowledge_content_parts.append(f"  - Category: {semantic_metadata.get('categories')}")
                        if semantic_metadata.get("sub_category_level_1"):
                            knowledge_content_parts.append("  - Sub-categories:")
                            knowledge_content_parts.append(f"    * Level 1: {semantic_metadata.get('sub_category_level_1')}")
                            if semantic_metadata.get("sub_category_level_2"):
                                knowledge_content_parts.append(f"    * Level 2: {semantic_metadata.get('sub_category_level_2')}")
                            if semantic_metadata.get("sub_category_level_3"):
                                knowledge_content_parts.append(f"    * Level 3: {semantic_metadata.get('sub_category_level_3')}")
                        if semantic_metadata.get("topics"):
                            knowledge_content_parts.append(f"  - Topics: {semantic_metadata.get('topics')}")

                knowledge_content_parts.append("")

            # Format block
            result_id = f"{virtual_record_id}_{result.get('block_index', 0)}"
            if result_id in seen_blocks:
                continue
            seen_blocks.add(result_id)

            block_type = result.get("block_type")
            block_index = result.get("block_index", 0)
            block_number = f"R{record_number}-{block_index}"

            # Store block_number in the result for citation processing
            result["block_number"] = block_number

            content = result.get("content", "")

            # Skip images unless multimodal
            if block_type == BlockType.IMAGE.value:
                continue

            # Format block with proper structure
            if block_type == GroupType.TABLE.value:
                # Handle table blocks
                table_summary, child_results = result.get("content", ("", []))
                knowledge_content_parts.append(f"* Block Group Number: {block_number}")
                knowledge_content_parts.append("* Block Group Type: table")
                knowledge_content_parts.append(f"* Table Summary: {table_summary}")
                knowledge_content_parts.append("* Table Rows/Blocks:")
                for child in child_results[:5]:  # Limit table rows
                    child_block_index = child.get("block_index", 0)
                    child_block_number = f"R{record_number}-{child_block_index}"
                    knowledge_content_parts.append(f"  - Block Number: {child_block_number}")
                    knowledge_content_parts.append(f"  - Block Content: {child.get('content', '')}")
            else:
                # Regular block
                knowledge_content_parts.append(f"* Block Number: {block_number}")
                knowledge_content_parts.append(f"* Block Type: {block_type}")
                knowledge_content_parts.append(f"* Block Content: {content}")

            knowledge_content_parts.append("")

        # Close last record
        if record_number > 0:
            knowledge_content_parts.append("</record>")

        knowledge_content = "\n".join(knowledge_content_parts)

        # Create tool result entry
        tool_call_id = f"call_knowledge_retrieval_{uuid.uuid4().hex[:8]}"

        tool_result = {
            "tool_name": "internal_knowledge_retrieval",
            "result": knowledge_content,
            "status": "success",
            "tool_id": tool_call_id,
            "args": {"query": state.get("query", ""), "result_count": len(final_results)},
            "execution_timestamp": datetime.now().isoformat(),
            "iteration": 0
        }

        # Create ToolMessage (this represents the result of the knowledge retrieval "tool call")
        tool_message = ToolMessage(content=knowledge_content, tool_call_id=tool_call_id)

        # Initialize messages if not present
        if "messages" not in state:
            state["messages"] = []

        # Create a corresponding AIMessage with tool_call to make it look like the system "called" the retrieval
        # This makes the flow more natural: system "calls" retrieval -> gets result
        from langchain_core.messages import AIMessage
        ai_message_with_tool_call = AIMessage(
            content="",
            tool_calls=[{
                "id": tool_call_id,
                "name": "internal_knowledge_retrieval",
                "args": {"query": state.get("query", ""), "result_count": len(final_results)}
            }]
        )

        # Add both the tool call and the result to messages
        state["messages"].append(ai_message_with_tool_call)
        state["messages"].append(tool_message)

        # Initialize and add to all_tool_results
        if "all_tool_results" not in state:
            state["all_tool_results"] = []
        state["all_tool_results"].append(tool_result)

        logger_instance.info(f"✅ Injected {len(final_results)} knowledge blocks as tool result")

    except Exception as e:
        logger_instance.error(f"Error injecting knowledge as tool result: {str(e)}", exc_info=True)
        # Don't fail the whole retrieval if this fails

