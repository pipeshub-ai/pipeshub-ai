"""
Retrieval Module

Handles knowledge base retrieval for the QNA agent.
Determines when retrieval is needed and executes vector search
across configured knowledge bases and connectors.
"""

import logging

from langgraph.types import StreamWriter

from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)


async def conditional_retrieve_node(state: ChatState, writer: StreamWriter) -> ChatState:
    """
    Smart retrieval based on query analysis.

    Only performs retrieval when the query needs internal knowledge.
    For follow-up queries with existing context, skips retrieval.
    Args:
        state: Current chat state with query analysis
        writer: Stream writer for status updates

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
        logger_instance.info("Gathering knowledge sources...")
        writer({
            "event": "status",
            "data": {
                "status": "retrieving",
                "message": "Gathering knowledge sources..."
            }
        })

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

        # Deduplicate results
        seen_ids = set()
        final_results = []
        for result in search_results:
            result_id = result["metadata"].get("_id")
            if result_id not in seen_ids:
                seen_ids.add(result_id)
                final_results.append(result)

        # Store results
        state["search_results"] = search_results
        state["final_results"] = final_results[:adjusted_limit]

        logger_instance.debug(f"Final deduplicated results: {len(state['final_results'])}")

        # Clean up retrieval artifacts to reduce state pollution
        from app.modules.agents.qna.chat_state import cleanup_state_after_retrieval
        cleanup_state_after_retrieval(state)
        logger_instance.debug("Cleaned up retrieval artifacts")

        return state

    except Exception as e:
        logger.error(f"Error in retrieval: {str(e)}", exc_info=True)
        state["error"] = {"status_code": 500, "detail": f"Retrieval error: {str(e)}"}
        return state

